"""Worker: claims queued jobs from the orchestrator DB and runs them.

A job is either a one-shot workflow run, or - if it names a queue
(`queue_name`) - a "process transaction" loop: pull one item at a time from
that queue, run the workflow with that item's payload seeded into the engine's
variables (so `{item.<field>}` placeholders resolve - see engine.py's
substitute_variables), and mark the item success/failed (retrying after a
backoff, see db.retry_delay_seconds) before moving on to the next one, until
the queue is empty or a stop is requested. A queue-driven job deliberately
keeps going past a failing item, but its final status still reflects them: it
only reports success if every item it touched ended up succeeding.

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
from ..models import Workflow, resolve_sub_workflows

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


def _run_workflow_once(
    job_id: str,
    workflow: Workflow,
    variables: dict[str, Any] | None = None,
    sub_workflows: dict[str, Workflow] | None = None,
) -> None:
    # Tracks whether this run was ever paused at a breakpoint - if the user then
    # clicks "Stoppen" while paused there (mid-debug), we deliberately skip
    # backend.close() below so the browser/desktop app stays open exactly where
    # they left it, instead of yanking it away right as they start inspecting.
    reached_breakpoint = False

    def on_breakpoint(index: int, step, variables: dict[str, Any], path: str) -> None:
        nonlocal reached_breakpoint
        reached_breakpoint = True
        db.set_paused(job_id, index, step.action, variables, path)
        while not db.wait_and_clear_resume(job_id):
            if db.is_stop_requested(job_id):
                break
            time.sleep(0.3)
        db.set_paused(job_id, None, None, path=None)

    backend = _make_backend(workflow)
    try:
        WorkflowEngine(backend).run(
            workflow,
            on_breakpoint=on_breakpoint,
            should_stop=lambda: db.is_stop_requested(job_id),
            variables=variables,
            # Read per run, not per job: editing a global takes effect on the
            # next run without re-queuing anything.
            global_variables=db.get_global_variables(),
            sub_workflows=sub_workflows,
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
    sub_workflows = {
        name: Workflow.from_raw(raw) for name, raw in json.loads(job.get("sub_workflows_json") or "{}").items()
    }
    queue_name = job["queue_name"]

    try:
        failed = 0
        processed = 0
        if queue_name:
            processed, failed = _run_queue_driven(job_id, workflow_dict, queue_name, sub_workflows)
        else:
            logger.info("Running job '%s'", job["name"])
            _run_workflow_once(job_id, Workflow.from_raw(workflow_dict), sub_workflows=sub_workflows)
        if db.is_stop_requested(job_id):
            db.finish_job(job_id, "cancelled")
        elif failed:
            # A queue-driven job keeps going past a failing item on purpose, but
            # it must not then report "success" - the job is only successful if
            # every item it processed ended up succeeding.
            db.finish_job(job_id, "error", f"{failed} of {processed} queue item(s) failed permanently")
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


def _sleep_unless_stopped(job_id: str, seconds: float, tick: float = 0.5) -> bool:
    """Sleeps up to `seconds`, in small slices so a stop request is still picked
    up promptly. Returns False if a stop was requested while waiting."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        if db.is_stop_requested(job_id):
            return False
        time.sleep(min(tick, remaining))


def _run_queue_driven(
    job_id: str,
    workflow_dict: dict[str, Any],
    queue_name: str,
    sub_workflows: dict[str, Workflow] | None = None,
) -> tuple[int, int]:
    """Processes the queue until it's exhausted (or stopped), returning
    (processed, permanently_failed) so the caller can set the job's status from
    what actually happened to the items. Both counts are per *item*, not per
    attempt - a retried item is still one item."""
    queue = db.get_queue_by_name(queue_name)
    if queue is None:
        raise RuntimeError(f"Queue '{queue_name}' does not exist")

    attempted: set[int] = set()
    failed = 0
    while True:
        if db.is_stop_requested(job_id):
            logger.info("Job stopped; processed %d item(s)", len(attempted))
            return len(attempted), failed
        item = db.claim_next_queue_item(queue["id"], job_id)
        if item is None:
            # Nothing claimable *right now* isn't the same as an empty queue:
            # items awaiting their retry backoff are still ours to process, so
            # wait them out rather than ending the job with work outstanding.
            wait_seconds = db.seconds_until_next_retry(queue["id"])
            if wait_seconds is None:
                logger.info("Queue '%s' empty; processed %d item(s)", queue_name, len(attempted))
                return len(attempted), failed
            logger.info("Queue '%s': waiting %.0fs for the next retry", queue_name, wait_seconds)
            if not _sleep_unless_stopped(job_id, wait_seconds):
                logger.info("Job stopped; processed %d item(s)", len(attempted))
                return len(attempted), failed
            continue

        payload = json.loads(item["payload"])
        attempted.add(item["id"])
        logger.info("[item %d] %s", item["id"], payload)
        try:
            _run_workflow_once(
                job_id, Workflow.from_raw(workflow_dict), variables={"item": payload}, sub_workflows=sub_workflows
            )
            db.complete_queue_item(item["id"], True, output={})
        except WorkflowCancelled:
            # A user-requested stop says nothing about the item - hand it back
            # untouched (no retry consumed) so the next run picks it up again.
            db.release_queue_item(item["id"])
            logger.info("[item %d] released after stop request", item["id"])
            return len(attempted), failed
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole queue
            status = db.complete_queue_item(item["id"], False, error_message=str(exc))
            if status == "failed":
                failed += 1
                logger.error("[item %d] failed permanently: %s", item["id"], exc)
            else:
                logger.warning("[item %d] failed, queued for retry: %s", item["id"], exc)


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
            workflow_obj = Workflow.from_raw(json.loads(schedule["workflow_json"]))
            db.create_job(
                schedule["name"],
                workflow_obj.to_dict(),
                queue_name=schedule["queue_name"],
                sub_workflows=resolve_sub_workflows(workflow_obj),
            )
            db.mark_schedule_ran(schedule["id"])
            logger.info("Schedule '%s' fired -> new job enqueued", schedule["name"])
        if stop_event is not None:
            stop_event.wait(poll_interval)
        else:
            time.sleep(poll_interval)
