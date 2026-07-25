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
from werkzeug.security import check_password_hash

from .. import models
from ..models import Workflow, resolve_sub_workflows
from ..orchestrator import db
from .schema import ACTION_SCHEMAS, activity_catalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

# `global` and `item` are placeholder namespaces, not variable names - a global
# called either would be unreachable as {global.item} resolves against the
# namespace, not the value (see engine.py's _NAMESPACE_KEYS).
_RESERVED_GLOBAL_NAMES = ("global", "item", "var")

# Multi-user RBAC (see require_login/db.any_users_exist): viewer < operator <
# admin. Only consulted once at least one user account exists - the default,
# frictionless single-user mode has no notion of roles at all.
_ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}


def _required_role(method: str, path: str) -> str:
    """Minimum role a request needs, once multi-user mode is active.
    Installation-wide, sensitive configuration (accounts, credentials, global
    variables) is admin-only; any other state-changing request needs at least
    "operator"; a plain read (GET) only needs to be logged in at all
    ("viewer")."""
    if (
        path.startswith("/api/users")
        or path.startswith("/api/credentials")
        or path.startswith("/api/globals")
        or path.startswith("/api/audit-log")
        or path.startswith("/api/notifications")
    ):
        return "admin"
    if path.startswith("/api/worker/"):
        # A remote worker executes workflows and reads global variables via
        # this namespace (see remote_store.RemoteStore) - operational access,
        # not a plain read, regardless of HTTP method.
        return "operator"
    if method == "GET":
        return "viewer"
    return "operator"

# One entry per in-flight recording session (unaffected by the orchestrator -
# a recording is a live interactive picking session tied to one browser tab,
# not a durable/queueable unit of work).
_recordings: dict[str, Any] = {}


def _safe_workflow_path(name: str) -> Path:
    # models.workflow_path is the single resolver, shared with the engine's
    # `run_workflow` action - otherwise the Studio could save a sub-workflow
    # into a different directory than the one a run resolves names against.
    return models.workflow_path(name)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False  # preserve schema.py's action order (e.g. "navigate" before "click")
    models.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    # Login is entirely opt-in: this Studio is a local single-user MVP tool by
    # default (zero friction, matching every earlier session). Setting
    # UIFLOW_STUDIO_PASSWORD adds a single shared-password gate in front of it -
    # e.g. for the case where the Studio is bound to a non-loopback host and
    # reachable by others. The moment `uiflow create-user` has created at least
    # one account (db.any_users_exist), per-account login and role checks
    # (_required_role/_ROLE_ORDER) take over from *both* of the above - a real
    # multi-user/RBAC system, opted into the same way credentials/globals are:
    # by using the feature, not by an env var toggle.
    studio_password = os.environ.get("UIFLOW_STUDIO_PASSWORD")
    app.secret_key = os.environ.get("UIFLOW_STUDIO_SECRET_KEY") or secrets.token_hex(32)

    @app.before_request
    def require_login() -> Response | None:
        if request.path in ("/login", "/logout", "/api/me") or request.path.startswith("/static/"):
            return None
        if db.any_users_exist():
            username = session.get("username")
            if not username:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "unauthenticated"}), 401
                return redirect("/login")
            role = session.get("role", "viewer")
            required = _required_role(request.method, request.path)
            if _ROLE_ORDER.get(role, -1) < _ROLE_ORDER[required]:
                return jsonify({"error": "forbidden"}), 403
            return None
        if not studio_password:
            return None
        if session.get("authenticated"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthenticated"}), 401
        return redirect("/login")

    @app.after_request
    def audit_log(response: Response) -> Response:
        # Every state-changing API call, regardless of outcome - a rejected
        # attempt (401/403/400) is exactly as auditable as a successful one.
        # GETs (plain reads) are deliberately not logged, matching how
        # _required_role treats them - noisy and rarely what an audit trail is
        # for. request.path already names the target for almost every route
        # (e.g. "DELETE /api/users/bob"), see the audit_log table's own
        # comment in orchestrator/db.py. /api/worker/* is excluded too - that's
        # a worker process's own claim/heartbeat/log traffic (every 15s per
        # running job, or more), not an administrative action; the job/queue
        # tables are already that traffic's own durable record.
        if (
            request.method != "GET"
            and not request.path.startswith("/api/worker/")
            and (request.path.startswith("/api/") or request.path == "/login")
        ):
            db.add_audit_entry(
                session.get("username"),
                session.get("role"),
                f"{request.method} {request.path}",
                response.status_code,
            )
        return response

    @app.get("/login")
    def login_form() -> Response:
        return send_from_directory(STATIC_DIR, "login.html")

    @app.post("/login")
    def login_submit() -> Response:
        data = request.form or request.get_json(silent=True) or {}
        if db.any_users_exist():
            username = (data.get("username") or "").strip()
            user = db.get_user(username)
            if user and check_password_hash(user["password_hash"], data.get("password", "")):
                session["username"] = username
                session["role"] = user["role"]
                return redirect("/")
            return redirect("/login?error=1")
        if studio_password and secrets.compare_digest(data.get("password", ""), studio_password):
            session["authenticated"] = True
            return redirect("/")
        return redirect("/login?error=1")

    @app.post("/logout")
    def logout() -> Response:
        session.pop("authenticated", None)
        session.pop("username", None)
        session.pop("role", None)
        return redirect("/login" if (studio_password or db.any_users_exist()) else "/")

    @app.get("/api/me")
    def whoami() -> Response:
        if db.any_users_exist():
            username = session.get("username")
            return jsonify({"username": username, "role": session.get("role") if username else None, "multiuser": True})
        logged_in = (not studio_password) or bool(session.get("authenticated"))
        return jsonify({"username": None, "role": "admin" if logged_in else None, "multiuser": False})

    @app.get("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str) -> Response:
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/api/schema")
    def schema() -> Response:
        return jsonify(ACTION_SCHEMAS)

    @app.get("/api/activities")
    def activities() -> Response:
        """Palette metadata (label/category/description/keywords) for the
        builder's activity catalog - kept apart from /api/schema, which stays
        the plain action -> fields mapping the property forms are built from."""
        return jsonify(activity_catalog())

    @app.get("/api/workflows")
    def list_workflows() -> Response:
        from ..object_repository import REPOSITORY_FILENAME

        names = sorted(
            p.stem for p in models.WORKFLOWS_DIR.glob("*.yaml") if p.name != REPOSITORY_FILENAME
        )
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
        # Saving the workflow you have open is meant to overwrite, so that stays
        # the default. Writing under a *different* name (rename, duplicate, "save
        # as") is not - it would destroy an unrelated workflow with no warning -
        # so those callers pass ?overwrite=false and handle the 409.
        if request.args.get("overwrite", "true").lower() in ("false", "0") and path.exists():
            return jsonify({"error": f"Workflow '{path.stem}' existiert bereits"}), 409
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
        job_id = db.create_job(
            workflow.name,
            workflow.to_dict(),
            queue_name=data.get("queue_name"),
            sub_workflows=resolve_sub_workflows(workflow),
        )
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
                        "path": controls["paused_step_path"],
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
            job.pop("sub_workflows_json", None)
        return jsonify(jobs)

    @app.get("/api/jobs/<job_id>")
    def get_job_detail(job_id: str) -> Response:
        job = db.get_job(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        job["workflow"] = json.loads(job.pop("workflow_json"))
        job["sub_workflows"] = json.loads(job.pop("sub_workflows_json", None) or "{}")
        return jsonify(job)

    @app.get("/api/jobs/<job_id>/logs")
    def get_job_logs(job_id: str) -> Response:
        if db.get_job(job_id) is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(db.get_logs(job_id))

    # --- remote worker API (see orchestrator/remote_store.py) ---------------
    #
    # Mirrors, one HTTP call each, the exact subset of orchestrator/db.py that
    # orchestrator/worker.py calls on its `store` parameter - so a worker
    # process with no filesystem access to orchestrator.db (a different
    # machine than this Studio server) can still claim jobs/queue items, log,
    # report breakpoints, and finish work, via RemoteStore instead of direct
    # imports of this module. Requires "operator" (see _required_role); a
    # remote worker authenticates the same way any other client does - see
    # RemoteStore.login - by logging in first and keeping the session cookie.

    @app.post("/api/worker/claim")
    def worker_claim_job() -> Response:
        data = request.get_json(force=True)
        return jsonify(db.claim_next_job(data["worker_id"]))

    @app.post("/api/worker/jobs/<job_id>/logs")
    def worker_add_log(job_id: str) -> Response:
        data = request.get_json(force=True)
        db.add_log(job_id, data["level"], data["message"])
        return jsonify({"ok": True})

    @app.post("/api/worker/jobs/<job_id>/heartbeat")
    def worker_heartbeat(job_id: str) -> Response:
        db.heartbeat_job(job_id)
        return jsonify({"ok": True})

    @app.get("/api/worker/jobs/<job_id>/control")
    def worker_job_control(job_id: str) -> Response:
        return jsonify({"stop_requested": db.is_stop_requested(job_id)})

    @app.post("/api/worker/jobs/<job_id>/resume_clear")
    def worker_job_resume_clear(job_id: str) -> Response:
        return jsonify({"resumed": db.wait_and_clear_resume(job_id)})

    @app.post("/api/worker/jobs/<job_id>/pause")
    def worker_job_pause(job_id: str) -> Response:
        data = request.get_json(force=True)
        db.set_paused(job_id, data.get("index"), data.get("action"), data.get("variables"), data.get("path"))
        return jsonify({"ok": True})

    @app.post("/api/worker/jobs/<job_id>/finish")
    def worker_job_finish(job_id: str) -> Response:
        data = request.get_json(force=True)
        db.finish_job(job_id, data["status"], data.get("error_message"))
        if data["status"] == "error":
            # The remote worker's own RemoteStore.notify_job_failed is a
            # no-op (it has no local notification settings/credentials to
            # read) - this is the one place that call was skipped for, so the
            # notification still has to fire from somewhere.
            job = db.get_job(job_id)
            if job is not None:
                db.notify_job_failed(job_id, job["name"], data.get("error_message"))
        return jsonify({"ok": True})

    @app.get("/api/worker/globals")
    def worker_globals() -> Response:
        return jsonify(db.get_global_variables())

    @app.get("/api/worker/queues/by-name")
    def worker_get_queue_by_name() -> Response:
        # null (not 404) when missing, like /api/worker/claim's "no job queued" -
        # this mirrors db.get_queue_by_name's own contract exactly, since
        # worker.py's _run_queue_driven only checks `queue is None`, never a
        # status code.
        return jsonify(db.get_queue_by_name(request.args.get("name", "")))

    @app.post("/api/worker/queues/<int:queue_id>/claim")
    def worker_claim_queue_item(queue_id: int) -> Response:
        data = request.get_json(force=True)
        return jsonify(db.claim_next_queue_item(queue_id, data["locked_by"]))

    @app.get("/api/worker/queues/<int:queue_id>/next_retry_wait")
    def worker_next_retry_wait(queue_id: int) -> Response:
        return jsonify({"seconds": db.seconds_until_next_retry(queue_id)})

    @app.post("/api/worker/queue_items/<int:item_id>/complete")
    def worker_complete_queue_item(item_id: int) -> Response:
        data = request.get_json(force=True)
        status = db.complete_queue_item(
            item_id,
            data["success"],
            output=data.get("output"),
            error_message=data.get("error_message"),
            permanent=data.get("permanent", False),
        )
        return jsonify({"status": status})

    @app.post("/api/worker/queue_items/<int:item_id>/release")
    def worker_release_queue_item(item_id: int) -> Response:
        db.release_queue_item(item_id)
        return jsonify({"ok": True})

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

    @app.post("/api/inspect/web")
    def inspect_web() -> Response:
        data = request.get_json(force=True) or {}
        url = data.get("url")
        selector = data.get("selector")
        if not url or not selector:
            return jsonify({"ok": False, "error": "url and selector required"}), 400
        from .picker import inspect_web_selector

        try:
            result = inspect_web_selector(url, selector)
            return jsonify({"ok": True, **result})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - surface any picker failure to the UI
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/inspect/desktop")
    def inspect_desktop() -> Response:
        data = request.get_json(force=True) or {}
        focus_title = data.get("focus_title")
        focus_path = data.get("focus_path")
        selector = {k: v for k, v in (data.get("selector") or {}).items() if v not in (None, "")}
        if not focus_title and not focus_path:
            return jsonify({"ok": False, "error": "focus_title or focus_path required"}), 400
        from .picker import inspect_desktop_selector

        try:
            result = inspect_desktop_selector(focus_title=focus_title, focus_path=focus_path, **selector)
            return jsonify({"ok": True, **result})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
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

    @app.get("/api/users")
    def list_users_route() -> Response:
        return jsonify(db.list_users())

    @app.post("/api/users")
    def create_user_route() -> Response:
        from werkzeug.security import generate_password_hash

        data = request.get_json(force=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        role = data.get("role") or "viewer"
        if not username or not password:
            return jsonify({"error": "username and password required"}), 400
        if role not in db.VALID_ROLES:
            return jsonify({"error": f"role must be one of {db.VALID_ROLES}"}), 400
        if db.get_user(username):
            return jsonify({"error": f"User '{username}' existiert bereits"}), 409
        db.create_user(username, generate_password_hash(password), role)
        return jsonify({"created": username, "role": role})

    @app.patch("/api/users/<username>")
    def update_user_route(username: str) -> Response:
        data = request.get_json(force=True) or {}
        if not db.get_user(username):
            return jsonify({"error": "not found"}), 404
        if "role" in data:
            if data["role"] not in db.VALID_ROLES:
                return jsonify({"error": f"role must be one of {db.VALID_ROLES}"}), 400
            if username == session.get("username") and data["role"] != "admin":
                # Refused, not just discouraged: an admin demoting themselves
                # could leave the installation with no admin account left to
                # undo it, locking everyone out of user management for good.
                return jsonify({"error": "Kann die eigene Admin-Rolle nicht selbst herabstufen"}), 400
            db.set_user_role(username, data["role"])
        if data.get("password"):
            from werkzeug.security import generate_password_hash

            db.set_user_password(username, generate_password_hash(data["password"]))
        return jsonify({"updated": username})

    @app.delete("/api/users/<username>")
    def delete_user_route(username: str) -> Response:
        if username == session.get("username"):
            return jsonify({"error": "Kann den eigenen Account nicht selbst löschen"}), 400
        db.delete_user(username)
        return jsonify({"deleted": username})

    @app.get("/api/audit-log")
    def get_audit_log() -> Response:
        limit = request.args.get("limit", default=200, type=int)
        return jsonify(db.list_audit_entries(limit=limit))

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

    @app.get("/api/globals")
    def list_globals() -> Response:
        return jsonify(db.list_global_variables())

    @app.post("/api/globals")
    def set_global_route() -> Response:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        if name in _RESERVED_GLOBAL_NAMES:
            return jsonify({"error": f"'{name}' ist ein reservierter Name"}), 400
        # The value arrives as text from the form; parse it as JSON when it looks
        # like JSON so a number stays a number and a list stays a list, and fall
        # back to the plain string otherwise (the common case).
        raw = data.get("value", "")
        if isinstance(raw, str):
            try:
                value: Any = json.loads(raw)
            except (TypeError, ValueError):
                value = raw
        else:
            value = raw
        db.set_global_variable(name, value)
        return jsonify({"saved": name, "value": value})

    @app.delete("/api/globals/<name>")
    def delete_global_route(name: str) -> Response:
        db.delete_global_variable(name)
        return jsonify({"deleted": name})

    @app.get("/api/repository")
    def list_repository_elements() -> Response:
        from .. import object_repository

        return jsonify(object_repository.list_elements())

    @app.post("/api/repository")
    def set_repository_element() -> Response:
        from .. import object_repository

        data = request.get_json(force=True) or {}
        scope = (data.get("scope") or "").strip()
        name = (data.get("name") or "").strip()
        fields = data.get("fields") or {}
        if not scope or not name:
            return jsonify({"error": "scope and name required"}), 400
        if not isinstance(fields, dict) or not fields:
            return jsonify({"error": "fields must be a non-empty mapping"}), 400
        object_repository.set_element(scope, name, fields)
        return jsonify({"saved": {"scope": scope, "name": name}})

    @app.delete("/api/repository/<scope>/<name>")
    def delete_repository_element(scope: str, name: str) -> Response:
        from .. import object_repository

        object_repository.delete_element(scope, name)
        return jsonify({"deleted": {"scope": scope, "name": name}})

    @app.post("/api/repository/<scope>/<name>/fallback")
    def add_repository_fallback(scope: str, name: str) -> Response:
        from .. import object_repository

        data = request.get_json(force=True) or {}
        fields = data.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            return jsonify({"error": "fields must be a non-empty mapping"}), 400
        object_repository.add_fallback(scope, name, fields)
        candidates = object_repository.get_element_candidates(scope, name)
        return jsonify({"saved": {"scope": scope, "name": name}, "candidates": candidates})

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

    @app.get("/api/notifications")
    def get_notification_settings_route() -> Response:
        return jsonify(db.get_notification_settings())

    @app.post("/api/notifications")
    def set_notification_settings_route() -> Response:
        data = request.get_json(force=True) or {}
        db.set_notification_settings(
            enabled=bool(data.get("enabled")),
            smtp_host=data.get("smtp_host") or None,
            smtp_port=int(data.get("smtp_port") or 587),
            use_tls=bool(data.get("use_tls", True)),
            username=data.get("username") or None,
            from_addr=data.get("from_addr") or None,
            to_addr=data.get("to_addr") or None,
            credential_name=data.get("credential_name") or None,
        )
        return jsonify(db.get_notification_settings())

    @app.post("/api/notifications/test")
    def send_test_notification_route() -> Response:
        try:
            db.send_notification_email(
                "uiflow: Testbenachrichtigung",
                "Falls diese E-Mail ankommt, ist die Konfiguration für Fehlerbenachrichtigungen korrekt.",
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin testing their SMTP config
            return jsonify({"error": str(exc)}), 400
        return jsonify({"sent": True})

    @app.get("/api/screenshot")
    def get_screenshot() -> Response:
        rel = request.args.get("path", "")
        target = (PROJECT_ROOT / rel).resolve()
        if PROJECT_ROOT not in target.parents or not target.exists():
            return jsonify({"error": "not found"}), 404
        return send_from_directory(target.parent, target.name)

    return app
