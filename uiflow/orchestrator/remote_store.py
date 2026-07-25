"""HTTP-backed job store for a worker that runs on a different machine than
the Studio server - the piece the roadmap called "Worker registrieren sich
per HTTP statt direktem DB-Zugriff". `orchestrator.worker`'s functions never
call the `db` module directly; they take a `store` parameter (default: the
`db` module itself) and call `store.claim_next_job(...)`, `store.add_log(...)`
etc. `RemoteStore` implements that exact same method surface over
studio/app.py's `/api/worker/*` endpoints, so `run_worker_loop(store=remote)`
is a drop-in replacement for the local, same-machine case - nothing about the
claiming/logging/retry logic in worker.py changes or needs to know which one
it's talking to.

Authentication reuses the Studio's existing login instead of a separate
API-key scheme: call `login()` once (shared UIFLOW_STUDIO_PASSWORD, or a
per-account username/password from `uiflow create-user ... --role operator`),
which stores the session cookie on the underlying `requests.Session` for
every later call - identical to how a browser tab stays logged in.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote


class RemoteStoreError(RuntimeError):
    """A worker-API call failed: network error, a non-2xx response, or (for
    login) credentials the server didn't accept."""


class RemoteStore:
    def __init__(self, base_url: str, session: Any = None):
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def login(self, password: str, username: str | None = None) -> None:
        """Authenticates the underlying session, same as submitting the
        Studio's login form. Works for both the shared UIFLOW_STUDIO_PASSWORD
        gate (username omitted) and per-account login (username given) - see
        studio/app.py's login_submit, which accepts either."""
        data: dict[str, str] = {"password": password}
        if username:
            data["username"] = username
        resp = self._session.post(f"{self.base_url}/login", data=data, allow_redirects=False)
        # A successful login redirects (302) to "/"; a rejected one redirects
        # back to "/login?error=1" - the status code alone doesn't distinguish
        # them (both are 302), so the redirect target does.
        location = resp.headers.get("Location", "")
        if resp.status_code not in (301, 302, 303, 307, 308) or "/login" in location:
            raise RemoteStoreError(f"Login to {self.base_url} was rejected")

    def _handle(self, resp: Any) -> Any:
        if resp.status_code >= 400:
            raise RemoteStoreError(f"{self.base_url}: HTTP {resp.status_code} - {getattr(resp, 'text', '')[:200]}")
        if not resp.content:
            return None
        return resp.json()

    def _get(self, path: str) -> Any:
        return self._handle(self._session.get(f"{self.base_url}{path}"))

    def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._handle(self._session.post(f"{self.base_url}{path}", json=body or {}))

    # --- the exact method surface orchestrator.worker calls on `store` -------

    def init_db(self) -> None:
        pass  # the server owns and initializes its own orchestrator.db

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        return self._post("/api/worker/claim", {"worker_id": worker_id})

    def add_log(self, job_id: str, level: str, message: str) -> None:
        self._post(f"/api/worker/jobs/{quote(job_id)}/logs", {"level": level, "message": message})

    def heartbeat_job(self, job_id: str) -> None:
        self._post(f"/api/worker/jobs/{quote(job_id)}/heartbeat")

    def is_stop_requested(self, job_id: str) -> bool:
        return bool(self._get(f"/api/worker/jobs/{quote(job_id)}/control")["stop_requested"])

    def wait_and_clear_resume(self, job_id: str) -> bool:
        return bool(self._post(f"/api/worker/jobs/{quote(job_id)}/resume_clear")["resumed"])

    def set_paused(
        self,
        job_id: str,
        index: int | None,
        action: str | None,
        variables: dict[str, Any] | None = None,
        path: str | None = None,
    ) -> None:
        self._post(
            f"/api/worker/jobs/{quote(job_id)}/pause",
            {"index": index, "action": action, "variables": variables, "path": path},
        )

    def finish_job(self, job_id: str, status: str, error_message: str | None = None) -> None:
        self._post(f"/api/worker/jobs/{quote(job_id)}/finish", {"status": status, "error_message": error_message})

    def get_global_variables(self) -> dict[str, Any]:
        return self._get("/api/worker/globals")

    def get_queue_by_name(self, name: str) -> dict[str, Any] | None:
        # A query parameter, not a path segment: a queue name could contain a
        # "/" (e.g. "invoices/2026"), which a path segment can't safely carry
        # through URL-decoding without risking an extra route segment.
        return self._get(f"/api/worker/queues/by-name?name={quote(name, safe='')}")

    def claim_next_queue_item(self, queue_id: int, locked_by: str) -> dict[str, Any] | None:
        return self._post(f"/api/worker/queues/{queue_id}/claim", {"locked_by": locked_by})

    def seconds_until_next_retry(self, queue_id: int) -> float | None:
        return self._get(f"/api/worker/queues/{queue_id}/next_retry_wait")["seconds"]

    def complete_queue_item(
        self,
        item_id: int,
        success: bool,
        output: dict[str, Any] | None = None,
        error_message: str | None = None,
        permanent: bool = False,
    ) -> str:
        result = self._post(
            f"/api/worker/queue_items/{item_id}/complete",
            {"success": success, "output": output, "error_message": error_message, "permanent": permanent},
        )
        return result["status"]

    def release_queue_item(self, item_id: int) -> None:
        self._post(f"/api/worker/queue_items/{item_id}/release")
