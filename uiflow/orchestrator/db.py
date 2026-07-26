"""Persistent orchestrator store (SQLite): jobs, their logs and pause/stop
control flags, and work-item queues. Replaces the Studio's old in-memory
`_jobs` dict so job state survives a server restart and can be picked up by a
separate `uiflow worker` process, not just a background thread in the same one.

Concurrency: WAL mode + short-lived connections-per-call, so the Studio's
embedded worker thread and one or more standalone `uiflow worker` processes
can all safely open the same file. `claim_next_job` / `claim_next_queue_item`
use an atomic UPDATE...WHERE so two workers racing for the same row is safe -
only one of them ever sees rowcount == 1.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent.parent / "orchestrator.db"

# Retry backoff bounds (see retry_delay_seconds). Kept small: a queue-driven job
# waits these out in-process, so the ceiling is what a single job can idle for.
RETRY_BACKOFF_BASE_SECONDS = 5.0
RETRY_BACKOFF_MAX_SECONDS = 60.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workflow_json TEXT NOT NULL,
    -- {name: workflow_dict} for every run_workflow reference this job's
    -- workflow had at enqueue time (see models.resolve_sub_workflows) - so a
    -- queued job keeps running the sub-workflow that existed when it was
    -- queued, not whatever the file happens to contain once a worker gets to
    -- it. '{}' (not referencing any sub-workflow, or none could be resolved)
    -- falls back to a live file lookup at run time, same as before this
    -- column existed.
    sub_workflows_json TEXT NOT NULL DEFAULT '{}',
    queue_name TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    worker_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);

CREATE TABLE IF NOT EXISTS job_controls (
    job_id TEXT PRIMARY KEY,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    resume_requested INTEGER NOT NULL DEFAULT 0,
    paused_step_index INTEGER,
    paused_step_action TEXT,
    paused_variables_json TEXT,
    paused_step_path TEXT
);

CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    priority INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    output TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    reference TEXT,
    locked_by TEXT,
    locked_at TEXT,
    -- Earliest time a retried item may be claimed again (see retry_delay_seconds).
    retry_after TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_items_queue_id ON queue_items(queue_id, status);

-- Only credential *names* are stored here - the secret value lives in the OS
-- credential store via the `keyring` package (see credentials.py). This table
-- exists purely so the Studio can list "which names have been set" without
-- ever reading a secret back out.
CREATE TABLE IF NOT EXISTS credentials (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Non-secret values shared by every workflow on this installation (base URLs,
-- mailboxes, thresholds). The sibling of the credentials table above: secrets
-- go there and never touch this database, everything else lives here so it is
-- edited in one place instead of in each workflow. Values are stored as JSON so
-- a number stays a number and a list stays a list.
CREATE TABLE IF NOT EXISTS global_variables (
    name TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    workflow_json TEXT NOT NULL,
    queue_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    skip_weekends INTEGER NOT NULL DEFAULT 0,
    skip_holidays INTEGER NOT NULL DEFAULT 0
);

-- Installation-wide (not per-schedule) list of dates a schedule may opt out
-- of firing on via its own skip_holidays flag - see
-- worker.py's _schedule_is_due, which treats this the same way it treats a
-- weekend when skip_weekends is set: an occurrence that falls on a listed
-- date is skipped, not fired late, and the *next* occurrence is what
-- eventually runs.
CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT
);

-- Individual accounts, opt-in: the Studio defaults to the frictionless
-- single-user mode (no login, or one shared UIFLOW_STUDIO_PASSWORD) that
-- every earlier session used. The moment one row exists here, studio/app.py
-- switches to per-user login and role checks instead - see
-- ROLE_ORDER/require_login. password_hash never stores a plain password
-- (werkzeug.security.generate_password_hash), matching how a credential's
-- secret value never touches this database either (see credentials.py).
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Every state-changing Studio API request (see studio/app.py's after_request
-- hook), regardless of outcome - a rejected attempt (403/401/400) is exactly
-- as auditable as a successful one. `username`/`role` are the acting
-- session's, both NULL outside multi-user mode (see users table above), where
-- there is no individual account to attribute the action to. `action` is
-- "METHOD /api/path", which - given this API's naming - already names the
-- target in almost every case (e.g. "DELETE /api/users/bob"), without having
-- to duplicate that per-route.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    username TEXT,
    role TEXT,
    action TEXT NOT NULL,
    status_code INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);

-- Singleton (id is always 1) installation-wide config for the proactive
-- "a job failed" e-mail notification (see notify_job_failed) - separate from
-- a workflow's own `send_email` step, which is per-workflow and requires an
-- author to build it in explicitly. The SMTP password itself is never stored
-- here - `credential_name` names an existing entry in the credentials table
-- (see credentials.py), resolved through the same OS keyring every
-- `get_credential` step already uses.
CREATE TABLE IF NOT EXISTS notification_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    smtp_host TEXT,
    smtp_port INTEGER NOT NULL DEFAULT 587,
    use_tls INTEGER NOT NULL DEFAULT 1,
    username TEXT,
    from_addr TEXT,
    to_addr TEXT,
    credential_name TEXT,
    updated_at TEXT
);

-- One row per prior save of a workflow (see studio/app.py's save_workflow,
-- which archives the file's *current* content here before overwriting it -
-- the live YAML file in workflows/ is always the newest version, so it is
-- never duplicated into this table). `saved_by` is the acting session's
-- username, NULL outside multi-user mode - same convention as audit_log.
CREATE TABLE IF NOT EXISTS workflow_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_name TEXT NOT NULL,
    content_yaml TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    saved_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_name ON workflow_versions(workflow_name, id);

-- Per-user, per-workflow-folder role grants (see studio/app.py's
-- _effective_role) - opt-in on top of a user's global role (see the users
-- table): a user with zero rows here is entirely unaffected, their global
-- role applies to every workflow exactly as before this table existed. The
-- moment a user has *any* row, they become folder-scoped for workflow
-- access specifically (not queues/credentials/schedules/etc.): only
-- folders they hold a grant for (or an ancestor folder, e.g. a grant on
-- "Rechnungswesen" also covers "Rechnungswesen/Sub") are reachable at all -
-- everything else is a 403, not a silent fallback to their global role.
-- `folder` is "" for the top-level (workflows with no "/" in their name).
-- Never consulted for an admin, who always has full access.
CREATE TABLE IF NOT EXISTS folder_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    folder TEXT NOT NULL,
    role TEXT NOT NULL,
    UNIQUE(username, folder)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # busy_timeout (not just the connect-level `timeout`) is what actually makes
    # SQLite retry internally when another connection briefly holds the write
    # lock - e.g. two processes/threads (the Studio's embedded worker thread and
    # its own create_app()) calling init_db() at nearly the same moment.
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    last_error: sqlite3.OperationalError | None = None
    for _ in range(5):
        try:
            with connect() as conn:
                conn.executescript(_SCHEMA)
                # Additive migrations for DBs created before these columns existed -
                # CREATE TABLE IF NOT EXISTS above doesn't alter an already-existing
                # table, so older orchestrator.db files need these to pick them up.
                for table, column, coltype in (
                    ("job_controls", "paused_variables_json", "TEXT"),
                    ("job_controls", "paused_step_path", "TEXT"),
                    ("queue_items", "retry_after", "TEXT"),
                    ("jobs", "sub_workflows_json", "TEXT"),
                    ("jobs", "last_heartbeat_at", "TEXT"),
                    ("schedules", "skip_weekends", "INTEGER NOT NULL DEFAULT 0"),
                    ("schedules", "skip_holidays", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                    except sqlite3.OperationalError:
                        pass  # column already exists
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            time.sleep(0.2)
    raise last_error  # noqa: RSE102 - re-raising the last observed OperationalError


# --- jobs -----------------------------------------------------------------


def create_job(
    name: str,
    workflow: dict[str, Any],
    queue_name: str | None = None,
    sub_workflows: dict[str, Any] | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, name, workflow_json, sub_workflows_json, queue_name, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (job_id, name, json.dumps(workflow), json.dumps(sub_workflows or {}), queue_name, _now()),
        )
        conn.execute("INSERT INTO job_controls (job_id) VALUES (?)", (job_id,))
    return job_id


def claim_next_job(worker_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        job_id = row["id"]
        now = _now()
        cur = conn.execute(
            "UPDATE jobs SET status='running', worker_id=?, started_at=?, last_heartbeat_at=? "
            "WHERE id=? AND status='queued'",
            (worker_id, now, now, job_id),
        )
        if cur.rowcount == 0:
            return None  # another worker won the race
        # Read the update back on the *same* connection/transaction: a separate
        # connection (e.g. via get_job()) isn't guaranteed to see it yet since
        # this transaction hasn't committed until the `with` block exits.
        updated = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(updated)


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def finish_job(job_id: str, status: str, error_message: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, error_message=?, finished_at=? WHERE id=?",
            (status, error_message, _now(), job_id),
        )


# Heartbeat is written every ~20s while a job runs (see worker.py's _run_job) -
# not tied to any specific step timing, since a step can legitimately run much
# longer than that (a slow page, a paused breakpoint waiting on a human). Only
# a worker *process* dying stops the heartbeats; how long a single step takes
# is irrelevant to it.
STALE_JOB_TIMEOUT_SECONDS = 90.0


def heartbeat_job(job_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE jobs SET last_heartbeat_at=? WHERE id=? AND status='running'", (_now(), job_id))


def sweep_stale_jobs(timeout_seconds: float = STALE_JOB_TIMEOUT_SECONDS) -> list[str]:
    """Finds jobs whose worker has gone silent (no heartbeat within
    `timeout_seconds`, e.g. the worker process crashed or its machine died) and
    settles them: any queue item that job still held `in_progress` is handed
    back to the queue exactly like release_queue_item does (no retry
    consumed - the item itself was never actually attempted-and-failed, its
    worker just vanished), and the job itself is marked 'error' so it stops
    looking perpetually 'running'. A one-shot (non-queue-driven) job is *not*
    silently re-run - its side effects up to the crash are unknown, so
    "error, needs a human to re-trigger it" is the only safe automatic
    outcome. Returns the swept job ids (for logging by the caller).

    Called periodically from run_scheduler_loop (see worker.py) - the same
    maintenance loop that already runs continuously, embedded in `uiflow
    studio` by default or standalone via `uiflow scheduler`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
    with connect() as conn:
        stale = conn.execute(
            "SELECT id FROM jobs WHERE status='running' AND last_heartbeat_at IS NOT NULL AND last_heartbeat_at <= ?",
            (cutoff,),
        ).fetchall()
        job_ids = [row["id"] for row in stale]
        for job_id in job_ids:
            conn.execute(
                "UPDATE queue_items SET status='new', locked_by=NULL, locked_at=NULL, started_at=NULL, "
                "retry_after=NULL WHERE locked_by=? AND status='in_progress'",
                (job_id,),
            )
            conn.execute(
                "UPDATE jobs SET status='error', error_message=?, finished_at=? WHERE id=? AND status='running'",
                ("Worker-Heartbeat-Timeout - der Worker ist vermutlich abgestürzt", _now(), job_id),
            )
        return job_ids


# --- logs -------------------------------------------------------------------


def add_log(job_id: str, level: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job_logs (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, _now(), level, message),
        )


def get_logs(job_id: str, since_id: int = 0) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM job_logs WHERE job_id = ? AND id > ? ORDER BY id", (job_id, since_id)
        ).fetchall()
        return [dict(r) for r in rows]


# --- job controls (stop / breakpoint resume) --------------------------------


def request_stop(job_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE job_controls SET stop_requested=1 WHERE job_id=?", (job_id,))


def is_stop_requested(job_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT stop_requested FROM job_controls WHERE job_id=?", (job_id,)
        ).fetchone()
        return bool(row and row["stop_requested"])


def request_resume(job_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE job_controls SET resume_requested=1 WHERE job_id=?", (job_id,))


def wait_and_clear_resume(job_id: str) -> bool:
    """Returns True and clears the flag if a resume was requested."""
    with connect() as conn:
        row = conn.execute(
            "SELECT resume_requested FROM job_controls WHERE job_id=?", (job_id,)
        ).fetchone()
        if row and row["resume_requested"]:
            conn.execute("UPDATE job_controls SET resume_requested=0 WHERE job_id=?", (job_id,))
            return True
        return False


def set_paused(
    job_id: str,
    index: int | None,
    action: str | None,
    variables: dict[str, Any] | None = None,
    path: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE job_controls SET paused_step_index=?, paused_step_action=?, paused_variables_json=?, "
            "paused_step_path=? WHERE job_id=?",
            (index, action, json.dumps(variables) if variables is not None else None, path, job_id),
        )


def get_controls(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM job_controls WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None


# --- queues -------------------------------------------------------------------


def create_queue(name: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO queues (name, created_at) VALUES (?, ?)", (name, _now())
        )
        if cur.lastrowid and cur.rowcount:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM queues WHERE name=?", (name,)).fetchone()
        return row["id"]


def get_queue_by_name(name: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM queues WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None


def delete_queue(queue_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM queue_items WHERE queue_id=?", (queue_id,))
        conn.execute("DELETE FROM queues WHERE id=?", (queue_id,))


def list_queues() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT q.id, q.name, q.created_at,
                   SUM(CASE WHEN qi.status='new' THEN 1 ELSE 0 END) AS new_count,
                   SUM(CASE WHEN qi.status='in_progress' THEN 1 ELSE 0 END) AS in_progress_count,
                   SUM(CASE WHEN qi.status='success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN qi.status='failed' THEN 1 ELSE 0 END) AS failed_count,
                   COUNT(qi.id) AS total_count
            FROM queues q
            LEFT JOIN queue_items qi ON qi.queue_id = q.id
            GROUP BY q.id
            ORDER BY q.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def add_queue_items(queue_id: int, items: list[dict[str, Any]]) -> int:
    now = _now()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO queue_items (queue_id, payload, priority, reference, max_retries, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    queue_id,
                    json.dumps(item.get("payload", {})),
                    int(item.get("priority", 0)),
                    item.get("reference"),
                    int(item.get("max_retries", 3)),
                    now,
                )
                for item in items
            ],
        )
    return len(items)


def list_queue_items(queue_id: int, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM queue_items WHERE queue_id=? AND status=? ORDER BY id DESC LIMIT ?",
                (queue_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM queue_items WHERE queue_id=? ORDER BY id DESC LIMIT ?",
                (queue_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def claim_next_queue_item(queue_id: int, locked_by: str) -> dict[str, Any] | None:
    with connect() as conn:
        # retry_after gates items that failed and are waiting out their backoff.
        # Comparing the timestamps as strings is safe because every one of them
        # is written by _now()/isoformat(): zero-padded fields in a fixed order,
        # always the same UTC offset, and the optional ".microseconds" sorts
        # below the "+" of the offset - so string order is chronological order.
        row = conn.execute(
            "SELECT id FROM queue_items WHERE queue_id=? AND status='new' "
            "AND (retry_after IS NULL OR retry_after <= ?) "
            "ORDER BY priority DESC, id LIMIT 1",
            (queue_id, _now()),
        ).fetchone()
        if row is None:
            return None
        item_id = row["id"]
        cur = conn.execute(
            "UPDATE queue_items SET status='in_progress', locked_by=?, locked_at=?, started_at=? "
            "WHERE id=? AND status='new'",
            (locked_by, _now(), _now(), item_id),
        )
        if cur.rowcount == 0:
            return None  # another worker won the race
        item = conn.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        return dict(item)


def retry_delay_seconds(retry_count: int) -> float:
    """Exponential backoff before a failed item may be retried. Without it a
    deterministically failing item is re-claimed as fast as the worker can loop,
    burning through every retry in milliseconds - which defeats the point, since
    retries exist for *transient* faults (a slow page, a flaky network) that need
    time to clear. Capped so a queue-driven job never stalls for long."""
    return min(RETRY_BACKOFF_BASE_SECONDS * (2 ** max(retry_count - 1, 0)), RETRY_BACKOFF_MAX_SECONDS)


def complete_queue_item(
    item_id: int,
    success: bool,
    output: dict[str, Any] | None = None,
    error_message: str | None = None,
    permanent: bool = False,
) -> str:
    """Marks an item done, returning its resulting status ('success', 'new' if
    it will be retried, or 'failed' once the retries are used up).

    `permanent=True` (a business error - see engine.py's BusinessError/`fail`
    action) marks the item 'failed' immediately: no retry consumed, no backoff
    scheduled. A failure that would be the identical failure on a second
    attempt (an invalid invoice doesn't become valid by retrying) must not eat
    into `max_retries` the way a transient/technical one should."""
    with connect() as conn:
        if success:
            conn.execute(
                "UPDATE queue_items SET status='success', output=?, finished_at=? WHERE id=?",
                (json.dumps(output or {}), _now(), item_id),
            )
            return "success"
        if permanent:
            conn.execute(
                "UPDATE queue_items SET status='failed', error_message=?, "
                "locked_by=NULL, locked_at=NULL, retry_after=NULL, finished_at=? WHERE id=?",
                (error_message, _now(), item_id),
            )
            return "failed"
        row = conn.execute(
            "SELECT retry_count, max_retries FROM queue_items WHERE id=?", (item_id,)
        ).fetchone()
        retry_count = (row["retry_count"] if row else 0) + 1
        max_retries = row["max_retries"] if row else 0
        next_status = "new" if retry_count <= max_retries else "failed"
        retry_after = None
        if next_status == "new":
            delay = retry_delay_seconds(retry_count)
            retry_after = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        conn.execute(
            "UPDATE queue_items SET status=?, retry_count=?, error_message=?, "
            "locked_by=NULL, locked_at=NULL, retry_after=?, finished_at=? WHERE id=?",
            (
                next_status,
                retry_count,
                error_message,
                retry_after,
                _now() if next_status == "failed" else None,
                item_id,
            ),
        )
        return next_status


def release_queue_item(item_id: int) -> None:
    """Hands a claimed item back unprocessed - used when a run is stopped
    mid-item, where nothing has been learned about the item itself and it must
    therefore not consume a retry (nor carry the backoff of an earlier one)."""
    with connect() as conn:
        conn.execute(
            "UPDATE queue_items SET status='new', locked_by=NULL, locked_at=NULL, started_at=NULL, "
            "retry_after=NULL WHERE id=? AND status='in_progress'",
            (item_id,),
        )


def seconds_until_next_retry(queue_id: int) -> float | None:
    """How long until the earliest backed-off item in this queue is claimable,
    or None if the queue holds no such item (i.e. it really is exhausted)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MIN(retry_after) AS next_at FROM queue_items "
            "WHERE queue_id=? AND status='new' AND retry_after IS NOT NULL",
            (queue_id,),
        ).fetchone()
    if row is None or row["next_at"] is None:
        return None
    delta = (datetime.fromisoformat(row["next_at"]) - datetime.now(timezone.utc)).total_seconds()
    return max(delta, 0.0)


# --- credential names (secret values live in the OS keyring, not here) -----


def add_credential_name(name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO credentials (name, created_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (name, _now()),
        )


def list_credential_names() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT name FROM credentials ORDER BY name").fetchall()
        return [r["name"] for r in rows]


def delete_credential_name(name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM credentials WHERE name=?", (name,))


# --- global variables (see the global_variables table comment) --------------


def set_global_variable(name: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO global_variables (name, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (name, json.dumps(value), _now()),
        )


def list_global_variables() -> list[dict[str, Any]]:
    """Name + decoded value per entry, for the Studio's management view."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM global_variables ORDER BY name").fetchall()
        return [{"name": r["name"], "value": json.loads(r["value_json"]), "updated_at": r["updated_at"]} for r in rows]


def get_global_variables() -> dict[str, Any]:
    """The flat name -> value mapping a run is seeded with."""
    return {entry["name"]: entry["value"] for entry in list_global_variables()}


def delete_global_variable(name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM global_variables WHERE name=?", (name,))


# --- schedules ---------------------------------------------------------------


def create_schedule(
    name: str,
    cron_expr: str,
    workflow: dict[str, Any],
    queue_name: str | None = None,
    skip_weekends: bool = False,
    skip_holidays: bool = False,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedules "
            "(name, cron_expr, workflow_json, queue_name, enabled, created_at, skip_weekends, skip_holidays) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (name, cron_expr, json.dumps(workflow), queue_name, _now(), int(skip_weekends), int(skip_holidays)),
        )
        return cur.lastrowid


def list_schedules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_schedule(schedule_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        return dict(row) if row else None


def set_schedule_enabled(schedule_id: int, enabled: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE schedules SET enabled=? WHERE id=?", (1 if enabled else 0, schedule_id))


def delete_schedule(schedule_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def mark_schedule_ran(schedule_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE schedules SET last_run_at=? WHERE id=?", (_now(), schedule_id))


# --- business calendar (holidays a schedule can opt out of, see the holidays
# table comment and worker.py's _schedule_is_due) ------------------------------


def add_holiday(date: str, name: str | None = None) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO holidays (date, name) VALUES (?, ?)", (date, name))


def list_holidays() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM holidays ORDER BY date").fetchall()
        return [dict(r) for r in rows]


def list_holiday_dates() -> set[str]:
    """Just the ISO date strings, for a fast membership check (see
    worker.py's _schedule_is_due) - the holiday's own name is display-only."""
    with connect() as conn:
        rows = conn.execute("SELECT date FROM holidays").fetchall()
        return {r["date"] for r in rows}


def delete_holiday(date: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM holidays WHERE date=?", (date,))


# --- users (opt-in per-account login/RBAC, see the users table comment) -----

VALID_ROLES = ("viewer", "operator", "admin")


def create_user(username: str, password_hash: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown role '{role}', expected one of {VALID_ROLES}")
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, _now()),
        )


def get_user(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    """Usernames and roles only - never the password hash, same spirit as
    list_credential_names() never returning a credential's secret value."""
    with connect() as conn:
        rows = conn.execute("SELECT username, role, created_at FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]


def set_user_role(username: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown role '{role}', expected one of {VALID_ROLES}")
    with connect() as conn:
        conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))


def set_user_password(username: str, password_hash: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?", (password_hash, username))


def delete_user(username: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))


def any_users_exist() -> bool:
    """Whether multi-user mode is active - see studio/app.py's require_login."""
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


# --- audit log ---------------------------------------------------------------


def add_audit_entry(username: str | None, role: str | None, action: str, status_code: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, username, role, action, status_code) VALUES (?, ?, ?, ?, ?)",
            (_now(), username, role, action, status_code),
        )


def list_audit_entries(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# --- proactive failure notification ------------------------------------------

_DEFAULT_NOTIFICATION_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "smtp_host": None,
    "smtp_port": 587,
    "use_tls": True,
    "username": None,
    "from_addr": None,
    "to_addr": None,
    "credential_name": None,
}


def get_notification_settings() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM notification_settings WHERE id=1").fetchone()
    if row is None:
        return dict(_DEFAULT_NOTIFICATION_SETTINGS)
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["use_tls"] = bool(result["use_tls"])
    return result


def set_notification_settings(
    enabled: bool,
    smtp_host: str | None,
    smtp_port: int,
    use_tls: bool,
    username: str | None,
    from_addr: str | None,
    to_addr: str | None,
    credential_name: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO notification_settings "
            "(id, enabled, smtp_host, smtp_port, use_tls, username, from_addr, to_addr, credential_name, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, smtp_host=excluded.smtp_host, "
            "smtp_port=excluded.smtp_port, use_tls=excluded.use_tls, username=excluded.username, "
            "from_addr=excluded.from_addr, to_addr=excluded.to_addr, credential_name=excluded.credential_name, "
            "updated_at=excluded.updated_at",
            (
                int(enabled),
                smtp_host,
                smtp_port,
                int(use_tls),
                username,
                from_addr,
                to_addr,
                credential_name,
                _now(),
            ),
        )


def send_notification_email(subject: str, body: str) -> None:
    """Sends via the installation-wide notification settings above. Raises if
    notifications aren't enabled/configured, or if the SMTP send itself fails
    - unlike notify_job_failed below, which wraps this for the "fire and
    forget from a job completion" case. Used directly by the Studio's "Test
    senden" button, where a real error is exactly what an admin fixing their
    SMTP config needs to see."""
    settings = get_notification_settings()
    if not settings["enabled"]:
        raise RuntimeError("Benachrichtigungen sind nicht aktiviert")
    if not settings["smtp_host"] or not settings["to_addr"]:
        raise RuntimeError("SMTP-Host und Empfänger müssen gesetzt sein")

    from .. import credentials
    from ..email_client import send_email

    password = ""
    if settings["credential_name"]:
        password = credentials.get_credential(settings["credential_name"])
    send_email(
        smtp_host=settings["smtp_host"],
        username=settings["username"] or "",
        password=password,
        to=settings["to_addr"],
        subject=subject,
        body=body,
        smtp_port=settings["smtp_port"],
        use_tls=settings["use_tls"],
        from_addr=settings["from_addr"],
    )


def notify_job_failed(job_id: str, job_name: str, error_message: str | None) -> None:
    """Best-effort - never raises, so a bad SMTP config or a network blip
    can't fail the job bookkeeping this is called from (see worker.py's
    _run_job and studio/app.py's remote-worker finish endpoint). Silently
    does nothing if notifications aren't enabled - that's the default,
    unconfigured state, not an error worth logging."""
    if not get_notification_settings()["enabled"]:
        return
    try:
        send_notification_email(
            f"uiflow: Job '{job_name}' fehlgeschlagen",
            f"Job-ID: {job_id}\nName: {job_name}\nFehler: {error_message or '(keine Meldung)'}",
        )
    except Exception:  # noqa: BLE001 - a notification hiccup must never fail the job itself
        import logging

        logging.getLogger("uiflow").warning("Fehlerbenachrichtigung konnte nicht gesendet werden", exc_info=True)


# --- workflow version history -------------------------------------------------

# Caps growth for a workflow that gets saved very often (e.g. scripted) -
# older versions beyond this are pruned on each new save. A version is a full
# YAML text snapshot, typically a few KB, so even 50 of them per workflow is
# not a meaningful amount of storage.
_MAX_VERSIONS_PER_WORKFLOW = 50


def add_workflow_version(workflow_name: str, content_yaml: str, saved_by: str | None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO workflow_versions (workflow_name, content_yaml, saved_at, saved_by) VALUES (?, ?, ?, ?)",
            (workflow_name, content_yaml, _now(), saved_by),
        )
        version_id = cur.lastrowid
        conn.execute(
            "DELETE FROM workflow_versions WHERE workflow_name=? AND id NOT IN "
            "(SELECT id FROM workflow_versions WHERE workflow_name=? ORDER BY id DESC LIMIT ?)",
            (workflow_name, workflow_name, _MAX_VERSIONS_PER_WORKFLOW),
        )
        return version_id


def list_workflow_versions(workflow_name: str) -> list[dict[str, Any]]:
    """Newest first, without `content_yaml` - kept light for a list view, the
    same way list_jobs() drops each job's workflow_json. Fetch a single
    version (get_workflow_version) to see its content."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, workflow_name, saved_at, saved_by FROM workflow_versions "
            "WHERE workflow_name=? ORDER BY id DESC",
            (workflow_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_workflow_version(version_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM workflow_versions WHERE id=?", (version_id,)).fetchone()
        return dict(row) if row else None


def delete_workflow_versions(workflow_name: str) -> None:
    """Called when the workflow itself is deleted (see studio/app.py's
    delete_workflow) - its history is meaningless once there is no longer a
    live file a restore could write back to."""
    with connect() as conn:
        conn.execute("DELETE FROM workflow_versions WHERE workflow_name=?", (workflow_name,))


# --- folder permissions (granular, opt-in per-user workflow-folder scoping,
# see the folder_permissions table comment and studio/app.py's
# _effective_role) ------------------------------------------------------------


def set_folder_permission(username: str, folder: str, role: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO folder_permissions (username, folder, role) VALUES (?, ?, ?) "
            "ON CONFLICT(username, folder) DO UPDATE SET role=excluded.role",
            (username, folder, role),
        )


def list_folder_permissions(username: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if username is not None:
            rows = conn.execute(
                "SELECT * FROM folder_permissions WHERE username=? ORDER BY folder", (username,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM folder_permissions ORDER BY username, folder").fetchall()
        return [dict(r) for r in rows]


def delete_folder_permission(username: str, folder: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM folder_permissions WHERE username=? AND folder=?", (username, folder))


def delete_folder_permissions_for_user(username: str) -> None:
    """Called when the user account itself is deleted (see studio/app.py's
    delete_user_route) - an orphaned grant for a username that no longer
    exists would just be dead weight."""
    with connect() as conn:
        conn.execute("DELETE FROM folder_permissions WHERE username=?", (username,))
