"""Worker: claims queued jobs from the orchestrator DB and runs them.

A job is either a one-shot workflow run, or - if it names a queue
(`queue_name`) - a "process transaction" loop: pull one item at a time from
that queue, run the workflow with that item's payload seeded into the engine's
variables (so `{item.<field>}` placeholders resolve - see engine.py's
substitute_variables), and mark the item success/failed (with retry) before
moving on to the next one, until the queue is empty or a stop is requested.

Threading note: this module intentionally does NOT run anything on a pynput
hook thread (that lesson - keep hook callbacks trivial, never let a second
thread touch UI Automation concurrently with one - is what studio/picker.py
and studio/recorder.py encode). The worker loop is a plain synchronous loop;
concurrency here is between separate *processes* (workers), coordinated only
through the SQLite job/queue tables.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from . import db
from ..engine import StepError, WorkflowCancelled, WorkflowEngine
from ..models import Workflow

logger = logging.getLogger("uiflow")


class _DbLogHandler(logging.Handler):
    """Persists this job's log records, filtered by thread id so a job only
    ever sees log lines produced while running *it* - same reasoning as
    studio/app.py's _QueueLogHandler, just writing to SQLite instead of an
    in-memory queue."""

    def __init__(self, job_id: str, thread_id: int):
        super().__init__()
        self._job_id = job_id
        self._thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._thread_id:
            return
        db.add_log(self._job_id, record.levelname, self.format(record))


def _make_backend(workflow: Workflow) -> Any:
    if workflow.backend == "web":
        from ..backends.web import WebBackend

        return WebBackend(channel=workflow.browser_channel)
    from ..backends.desktop import DesktopBackend

    return DesktopBackend()


def _run_workflow_once(job_id: str, workflow: Workflow, variables: dict[str, Any] | None = None) -> None:
    # Tracks whether this run was ever paused at a breakpoint - if the user then
    # clicks "Stoppen" while paused there (mid-debug), we deliberately skip
    # backend.close() below so the browser/desktop app stays open exactly where
    # they left it, instead of yanking it away right as they start inspecting.
    reached_breakpoint = False

    def on_breakpoint(index: int, step, variables: dict[str, Any]) -> None:
        nonlocal reached_breakpoint
        reached_breakpoint = True
        db.set_paused(job_id, index, step.action, variables)
        while not db.wait_and_clear_resume(job_id):
            if db.is_stop_requested(job_id):
                break
            time.sleep(0.3)
        db.set_paused(job_id, None, None)

    backend = _make_backend(workflow)
    try:
        WorkflowEngine(backend).run(
            workflow,
            on_breakpoint=on_breakpoint,
            should_stop=lambda: db.is_stop_requested(job_id),
            variables=variables,
        )
    finally:
        stopped_while_debugging = reached_breakpoint and db.is_stop_requested(job_id)
        if stopped_while_debugging:
            logger.info("Stopped while paused at a breakpoint - leaving the target application open")
        else:
            close = getattr(backend, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass


def _run_job(job: dict[str, Any]) -> None:
    job_id = job["id"]
    handler = _DbLogHandler(job_id, threading.get_ident())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    workflow_dict = json.loads(job["workflow_json"])
    queue_name = job["queue_name"]

    try:
        if queue_name:
            _run_queue_driven(job_id, workflow_dict, queue_name)
        else:
            logger.info("Running job '%s'", job["name"])
            _run_workflow_once(job_id, Workflow.from_raw(workflow_dict))
        if db.is_stop_requested(job_id):
            db.finish_job(job_id, "cancelled")
        else:
            db.finish_job(job_id, "success")
    except WorkflowCancelled:
        db.finish_job(job_id, "cancelled")
    except StepError as exc:
        logger.error(str(exc))
        db.finish_job(job_id, "error", str(exc))
    except Exception as exc:  # noqa: BLE001 - surface any failure instead of crashing the worker loop
        logger.error(str(exc))
        db.finish_job(job_id, "error", str(exc))
    finally:
        logger.removeHandler(handler)


def _run_queue_driven(job_id: str, workflow_dict: dict[str, Any], queue_name: str) -> None:
    queue = db.get_queue_by_name(queue_name)
    if queue is None:
        raise RuntimeError(f"Queue '{queue_name}' does not exist")

    processed = 0
    while True:
        if db.is_stop_requested(job_id):
            logger.info("Job stopped; processed %d item(s)", processed)
            return
        item = db.claim_next_queue_item(queue["id"], job_id)
        if item is None:
            logger.info("Queue '%s' empty; processed %d item(s)", queue_name, processed)
            return

        payload = json.loads(item["payload"])
        logger.info("[item %d] %s", item["id"], payload)
        try:
            _run_workflow_once(job_id, Workflow.from_raw(workflow_dict), variables={"item": payload})
            db.complete_queue_item(item["id"], True, output={})
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole queue
            logger.error("[item %d] failed: %s", item["id"], exc)
            db.complete_queue_item(item["id"], False, error_message=str(exc))
        processed += 1


def run_worker_loop(worker_id: str | None = None, poll_interval: float = 1.0, stop_event=None) -> None:
    """Blocks, repeatedly claiming and running queued jobs, until `stop_event`
    is set (if given) - used both by the standalone `uiflow worker` CLI command
    and by the Studio's embedded worker thread."""
    db.init_db()
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    logging.getLogger("uiflow").info("Worker '%s' started", worker_id)
    while stop_event is None or not stop_event.is_set():
        job = db.claim_next_job(worker_id)
        if job is None:
            time.sleep(poll_interval)
            continue
        _run_job(job)


def _schedule_is_due(schedule: dict[str, Any]) -> bool:
    from croniter import croniter

    last_run = schedule["last_run_at"]
    base = datetime.fromisoformat(last_run) if last_run else datetime.fromisoformat(schedule["created_at"])
    next_fire = croniter(schedule["cron_expr"], base).get_next(datetime)
    return next_fire <= datetime.now(next_fire.tzinfo)


def run_scheduler_loop(poll_interval: float = 20.0, stop_event=None) -> None:
    """Blocks, periodically checking enabled schedules (see orchestrator/db.py's
    `schedules` table) and enqueuing a job for any whose cron expression is due
    - a lightweight cron trigger, separate from run_worker_loop (which executes
    jobs) since a schedule only *creates* jobs, the regular worker loop (or a
    standalone `uiflow worker` process) still claims and runs them."""
    db.init_db()
    logger.info("Scheduler started")
    while stop_event is None or not stop_event.is_set():
        for schedule in db.list_schedules():
            if not schedule["enabled"]:
                continue
            try:
                due = _schedule_is_due(schedule)
            except Exception as exc:  # noqa: BLE001 - a bad cron expression must not kill the loop
                logger.error("Schedule '%s' has an invalid cron expression: %s", schedule["name"], exc)
                continue
            if not due:
                continue
            workflow = json.loads(schedule["workflow_json"])
            db.create_job(schedule["name"], workflow, queue_name=schedule["queue_name"])
            db.mark_schedule_ran(schedule["id"])
            logger.info("Schedule '%s' fired -> new job enqueued", schedule["name"])
        if stop_event is not None:
            stop_event.wait(poll_interval)
        else:
            time.sleep(poll_interval)
