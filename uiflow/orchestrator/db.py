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

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    workflow_json TEXT NOT NULL,
    queue_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    created_at TEXT NOT NULL
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


def create_job(name: str, workflow: dict[str, Any], queue_name: str | None = None) -> str:
    job_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, name, workflow_json, queue_name, status, created_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?)",
            (job_id, name, json.dumps(workflow), queue_name, _now()),
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
        cur = conn.execute(
            "UPDATE jobs SET status='running', worker_id=?, started_at=? "
            "WHERE id=? AND status='queued'",
            (worker_id, _now(), job_id),
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
    item_id: int, success: bool, output: dict[str, Any] | None = None, error_message: str | None = None
) -> str:
    """Marks an item done, returning its resulting status ('success', 'new' if
    it will be retried, or 'failed' once the retries are used up)."""
    with connect() as conn:
        if success:
            conn.execute(
                "UPDATE queue_items SET status='success', output=?, finished_at=? WHERE id=?",
                (json.dumps(output or {}), _now(), item_id),
            )
            return "success"
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


# --- schedules ---------------------------------------------------------------


def create_schedule(name: str, cron_expr: str, workflow: dict[str, Any], queue_name: str | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedules (name, cron_expr, workflow_json, queue_name, enabled, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (name, cron_expr, json.dumps(workflow), queue_name, _now()),
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
