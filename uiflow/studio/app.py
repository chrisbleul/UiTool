from __future__ import annotations

import json
import os
import queue
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session

from ..models import Workflow
from ..orchestrator import db
from .schema import ACTION_SCHEMAS

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# One entry per in-flight recording session (unaffected by the orchestrator -
# a recording is a live interactive picking session tied to one browser tab,
# not a durable/queueable unit of work).
_recordings: dict[str, Any] = {}


def _safe_workflow_path(name: str) -> Path:
    filename = Path(name).name  # discard any directory components
    if not filename.endswith(".yaml"):
        filename += ".yaml"
    return WORKFLOWS_DIR / filename


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False  # preserve schema.py's action order (e.g. "navigate" before "click")
    WORKFLOWS_DIR.mkdir(exist_ok=True)
    db.init_db()

    # Login is entirely opt-in: this Studio is a local single-user MVP tool by
    # default (zero friction, matching every earlier session), and a real
    # multi-user/RBAC system is out of scope here. Setting UIFLOW_STUDIO_PASSWORD
    # adds a single shared-password gate in front of it - e.g. for the case
    # where the Studio is bound to a non-loopback host and reachable by others.
    studio_password = os.environ.get("UIFLOW_STUDIO_PASSWORD")
    app.secret_key = os.environ.get("UIFLOW_STUDIO_SECRET_KEY") or secrets.token_hex(32)

    @app.before_request
    def require_login() -> Response | None:
        if not studio_password:
            return None
        if request.path in ("/login", "/logout") or request.path.startswith("/static/"):
            return None
        if session.get("authenticated"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthenticated"}), 401
        return redirect("/login")

    @app.get("/login")
    def login_form() -> Response:
        return send_from_directory(STATIC_DIR, "login.html")

    @app.post("/login")
    def login_submit() -> Response:
        data = request.form or request.get_json(silent=True) or {}
        if studio_password and secrets.compare_digest(data.get("password", ""), studio_password):
            session["authenticated"] = True
            return redirect("/")
        return redirect("/login?error=1")

    @app.post("/logout")
    def logout() -> Response:
        session.pop("authenticated", None)
        return redirect("/login" if studio_password else "/")

    @app.get("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str) -> Response:
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/api/schema")
    def schema() -> Response:
        return jsonify(ACTION_SCHEMAS)

    @app.get("/api/workflows")
    def list_workflows() -> Response:
        names = sorted(p.stem for p in WORKFLOWS_DIR.glob("*.yaml"))
        return jsonify(names)

    @app.get("/api/workflows/<name>")
    def get_workflow(name: str) -> Response:
        path = _safe_workflow_path(name)
        if not path.exists():
            return jsonify({"error": "not found"}), 404
        return jsonify(Workflow.load(path).to_dict())

    @app.post("/api/workflows/<name>")
    def save_workflow(name: str) -> Response:
        data = request.get_json(force=True)
        try:
            workflow = Workflow.from_raw(data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        path = _safe_workflow_path(name)
        workflow.save(path)
        return jsonify({"saved": path.name})

    @app.delete("/api/workflows/<name>")
    def delete_workflow(name: str) -> Response:
        path = _safe_workflow_path(name)
        if not path.exists():
            return jsonify({"error": "not found"}), 404
        path.unlink()
        return jsonify({"deleted": name})

    @app.post("/api/run")
    def run_workflow() -> Response:
        data = request.get_json(force=True)
        try:
            workflow = Workflow.from_raw(data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        job_id = db.create_job(workflow.name, workflow.to_dict(), queue_name=data.get("queue_name"))
        return jsonify({"job_id": job_id})

    @app.post("/api/run/<job_id>/continue")
    def continue_job(job_id: str) -> Response:
        if db.get_job(job_id) is None:
            return jsonify({"error": "unknown job"}), 404
        db.request_resume(job_id)
        return jsonify({"resumed": True})

    @app.post("/api/run/<job_id>/stop")
    def stop_job(job_id: str) -> Response:
        if db.get_job(job_id) is None:
            return jsonify({"error": "unknown job"}), 404
        db.request_stop(job_id)
        return jsonify({"stopping": True})

    @app.get("/api/run/<job_id>/stream")
    def stream_job(job_id: str) -> Response:
        if db.get_job(job_id) is None:
            return jsonify({"error": "unknown job"}), 404

        def generate():
            last_log_id = 0
            was_paused = False
            while True:
                for log in db.get_logs(job_id, since_id=last_log_id):
                    last_log_id = log["id"]
                    yield f"data: {json.dumps(log['message'])}\n\n"

                controls = db.get_controls(job_id)
                is_paused = bool(controls and controls["paused_step_index"] is not None)
                if is_paused and not was_paused:
                    variables = json.loads(controls["paused_variables_json"] or "{}")
                    payload = {
                        "index": controls["paused_step_index"],
                        "action": controls["paused_step_action"],
                        "variables": variables,
                    }
                    yield f"event: paused\ndata: {json.dumps(payload)}\n\n"
                was_paused = is_paused

                job = db.get_job(job_id)
                if job["status"] in ("success", "error", "cancelled"):
                    for log in db.get_logs(job_id, since_id=last_log_id):
                        last_log_id = log["id"]
                        yield f"data: {json.dumps(log['message'])}\n\n"
                    status_str = job["status"]
                    if status_str == "error" and job["error_message"]:
                        status_str = f"error:{job['error_message']}"
                    yield f"event: done\ndata: {json.dumps(status_str)}\n\n"
                    break

                time.sleep(0.4)

        return Response(generate(), mimetype="text/event-stream")

    @app.get("/api/jobs")
    def list_jobs() -> Response:
        status = request.args.get("status")
        jobs = db.list_jobs(status=status, limit=100)
        for job in jobs:
            job.pop("workflow_json", None)  # keep the list view light
        return jsonify(jobs)

    @app.get("/api/jobs/<job_id>")
    def get_job_detail(job_id: str) -> Response:
        job = db.get_job(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        job["workflow"] = json.loads(job.pop("workflow_json"))
        return jsonify(job)

    @app.get("/api/jobs/<job_id>/logs")
    def get_job_logs(job_id: str) -> Response:
        if db.get_job(job_id) is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(db.get_logs(job_id))

    @app.post("/api/queues")
    def create_queue() -> Response:
        data = request.get_json(force=True)
        name = data.get("name")
        if not name:
            return jsonify({"error": "name required"}), 400
        queue_id = db.create_queue(name)
        return jsonify({"id": queue_id, "name": name})

    @app.get("/api/queues")
    def list_queues() -> Response:
        return jsonify(db.list_queues())

    @app.post("/api/queues/<name>/items")
    def add_queue_items(name: str) -> Response:
        found = db.get_queue_by_name(name)
        queue_id = found["id"] if found else db.create_queue(name)
        data = request.get_json(force=True)
        items = data.get("items", [])
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items must be a non-empty list"}), 400
        count = db.add_queue_items(queue_id, items)
        return jsonify({"added": count})

    @app.get("/api/queues/<name>/items")
    def get_queue_items(name: str) -> Response:
        found = db.get_queue_by_name(name)
        if found is None:
            return jsonify({"error": "not found"}), 404
        status = request.args.get("status")
        return jsonify(db.list_queue_items(found["id"], status=status))

    @app.delete("/api/queues/<name>")
    def delete_queue_route(name: str) -> Response:
        found = db.get_queue_by_name(name)
        if found is None:
            return jsonify({"error": "not found"}), 404
        db.delete_queue(found["id"])
        return jsonify({"deleted": name})

    @app.post("/api/queues/<name>/import-excel")
    def import_excel_to_queue(name: str) -> Response:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "file required (multipart field 'file')"}), 400

        import tempfile

        from ..excel import read_excel_rows

        suffix = Path(upload.filename).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            upload.save(tmp.name)
            tmp_path = tmp.name
        try:
            rows = read_excel_rows(tmp_path, sheet=request.form.get("sheet") or None)
        except Exception as exc:  # noqa: BLE001 - surface any file/format error to the UI
            return jsonify({"error": str(exc)}), 400
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        if not rows:
            return jsonify({"error": "Excel file has no data rows"}), 400

        found = db.get_queue_by_name(name)
        queue_id = found["id"] if found else db.create_queue(name)
        count = db.add_queue_items(queue_id, [{"payload": row} for row in rows])
        return jsonify({"added": count})

    @app.post("/api/pick/web")
    def pick_web() -> Response:
        data = request.get_json(force=True) or {}
        url = data.get("url")
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        from .picker import pick_web_selector

        try:
            result = pick_web_selector(url, timeout=60.0)
            return jsonify({"ok": True, **result})
        except TimeoutError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 408
        except Exception as exc:  # noqa: BLE001 - surface any picker failure to the UI
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/pick/desktop")
    def pick_desktop() -> Response:
        data = request.get_json(silent=True) or {}
        from .picker import pick_desktop_element

        try:
            result = pick_desktop_element(
                timeout=30.0,
                delay=float(data.get("delay") or 0),
                focus_title=data.get("focus_title"),
                focus_path=data.get("focus_path"),
            )
            return jsonify({"ok": True, **result})
        except TimeoutError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 408
        except Exception as exc:  # noqa: BLE001 - surface any picker failure to the UI
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/record/start")
    def record_start() -> Response:
        data = request.get_json(silent=True) or {}
        focus_title = data.get("focus_title")
        focus_path = data.get("focus_path")
        if not focus_title and not focus_path:
            return jsonify({"ok": False, "error": "focus_title or focus_path required"}), 400

        from .recorder import Recorder

        recorder = Recorder()
        try:
            recorder.start(focus_title, focus_path)
        except Exception as exc:  # noqa: BLE001 - surface failure to the UI (e.g. app not running)
            return jsonify({"ok": False, "error": str(exc)}), 500

        record_id = uuid.uuid4().hex
        _recordings[record_id] = recorder
        return jsonify({"ok": True, "record_id": record_id})

    @app.post("/api/record/<record_id>/stop")
    def record_stop(record_id: str) -> Response:
        recorder = _recordings.get(record_id)
        if recorder is None:
            return jsonify({"error": "unknown recording"}), 404
        recorder.stop()
        return jsonify({"ok": True})

    @app.get("/api/record/<record_id>/stream")
    def record_stream(record_id: str) -> Response:
        recorder = _recordings.get(record_id)
        if recorder is None:
            return jsonify({"error": "unknown recording"}), 404

        def generate():
            while True:
                try:
                    event = recorder.events.get(timeout=30)
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if event.get("__stopped__"):
                    yield "event: stopped\ndata: {}\n\n"
                    del _recordings[record_id]
                    break
                yield f"event: step\ndata: {json.dumps(event)}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    @app.get("/api/credentials")
    def list_credentials() -> Response:
        return jsonify(db.list_credential_names())

    @app.post("/api/credentials")
    def set_credential_route() -> Response:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        value = data.get("value") or ""
        if not name or not value:
            return jsonify({"error": "name and value required"}), 400

        from ..credentials import set_credential

        try:
            set_credential(name, value)
        except Exception as exc:  # noqa: BLE001 - surface any keyring/backend error to the UI
            return jsonify({"error": str(exc)}), 500
        db.add_credential_name(name)
        return jsonify({"saved": name})

    @app.delete("/api/credentials/<name>")
    def delete_credential_route(name: str) -> Response:
        from ..credentials import delete_credential

        try:
            delete_credential(name)
        except Exception as exc:  # noqa: BLE001 - surface any keyring/backend error to the UI
            return jsonify({"error": str(exc)}), 500
        db.delete_credential_name(name)
        return jsonify({"deleted": name})

    @app.get("/api/schedules")
    def list_schedules() -> Response:
        schedules = db.list_schedules()
        for s in schedules:
            s.pop("workflow_json", None)
        return jsonify(schedules)

    @app.post("/api/schedules")
    def create_schedule() -> Response:
        data = request.get_json(force=True) or {}
        name = data.get("name")
        cron_expr = data.get("cron_expr")
        workflow_data = data.get("workflow")
        if not name or not cron_expr or not workflow_data:
            return jsonify({"error": "name, cron_expr and workflow required"}), 400
        try:
            workflow = Workflow.from_raw(workflow_data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        from croniter import CroniterBadCronError

        try:
            from croniter import croniter

            croniter(cron_expr)  # validate syntax before persisting
        except (CroniterBadCronError, ValueError) as exc:
            return jsonify({"error": f"Invalid cron expression: {exc}"}), 400

        schedule_id = db.create_schedule(name, cron_expr, workflow.to_dict(), queue_name=data.get("queue_name"))
        return jsonify({"id": schedule_id})

    @app.post("/api/schedules/<int:schedule_id>/toggle")
    def toggle_schedule(schedule_id: int) -> Response:
        schedule = db.get_schedule(schedule_id)
        if schedule is None:
            return jsonify({"error": "not found"}), 404
        db.set_schedule_enabled(schedule_id, not schedule["enabled"])
        return jsonify({"enabled": not schedule["enabled"]})

    @app.delete("/api/schedules/<int:schedule_id>")
    def delete_schedule_route(schedule_id: int) -> Response:
        db.delete_schedule(schedule_id)
        return jsonify({"deleted": schedule_id})

    @app.get("/api/screenshot")
    def get_screenshot() -> Response:
        rel = request.args.get("path", "")
        target = (PROJECT_ROOT / rel).resolve()
        if PROJECT_ROOT not in target.parents or not target.exists():
            return jsonify({"error": "not found"}), 404
        return send_from_directory(target.parent, target.name)

    return app
