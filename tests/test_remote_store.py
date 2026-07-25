"""Unit tests for RemoteStore against a fake requests.Session double - no real
network or server involved, same "fake the external dependency" approach as
tests/test_picker.py fakes Playwright/pywinauto. See tests/test_studio_app.py
for the server-side counterpart (the actual /api/worker/* Flask routes) and
tests/test_orchestrator.py for run_worker_loop/_run_job with a fake `store`
standing in for RemoteStore, proving worker.py itself never assumes `db`."""

import pytest

from uiflow.orchestrator.remote_store import RemoteStore, RemoteStoreError


class _FakeResponse:
    def __init__(self, status_code=200, json_body="__unset__", headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.text = ""
        self.content = b"" if json_body is None and status_code == 204 else b"x"

    def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, response):
        self._responses.append(response)

    def _record(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._record("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, kwargs)


@pytest.fixture
def fake():
    return _FakeSession()


def test_login_succeeds_on_a_redirect_to_root(fake):
    fake.queue(_FakeResponse(302, headers={"Location": "/"}))
    store = RemoteStore("http://host:8787", session=fake)

    store.login("secret")

    method, url, kwargs = fake.calls[0]
    assert method == "POST"
    assert url == "http://host:8787/login"
    assert kwargs["data"] == {"password": "secret"}


def test_login_with_username_includes_it(fake):
    fake.queue(_FakeResponse(302, headers={"Location": "/"}))
    store = RemoteStore("http://host:8787", session=fake)

    store.login("secret", username="alice")

    _, _, kwargs = fake.calls[0]
    assert kwargs["data"] == {"password": "secret", "username": "alice"}


def test_login_raises_when_redirected_back_to_login(fake):
    fake.queue(_FakeResponse(302, headers={"Location": "/login?error=1"}))
    store = RemoteStore("http://host:8787", session=fake)

    with pytest.raises(RemoteStoreError):
        store.login("wrong-password")


def test_login_raises_on_a_non_redirect_status(fake):
    fake.queue(_FakeResponse(200, headers={}))
    store = RemoteStore("http://host:8787", session=fake)

    with pytest.raises(RemoteStoreError):
        store.login("secret")


def test_claim_next_job_posts_worker_id_and_returns_the_job(fake):
    fake.queue(_FakeResponse(200, json_body={"id": "job-1", "status": "running"}))
    store = RemoteStore("http://host:8787", session=fake)

    job = store.claim_next_job("worker-1")

    assert job == {"id": "job-1", "status": "running"}
    method, url, kwargs = fake.calls[0]
    assert method == "POST"
    assert url == "http://host:8787/api/worker/claim"
    assert kwargs["json"] == {"worker_id": "worker-1"}


def test_claim_next_job_returns_none_when_nothing_queued(fake):
    fake.queue(_FakeResponse(200, json_body=None))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.claim_next_job("worker-1") is None


def test_add_log_posts_level_and_message(fake):
    fake.queue(_FakeResponse(200, json_body={"ok": True}))
    store = RemoteStore("http://host:8787", session=fake)

    store.add_log("job-1", "INFO", "hello")

    _, url, kwargs = fake.calls[0]
    assert url == "http://host:8787/api/worker/jobs/job-1/logs"
    assert kwargs["json"] == {"level": "INFO", "message": "hello"}


def test_is_stop_requested_reads_the_control_flag(fake):
    fake.queue(_FakeResponse(200, json_body={"stop_requested": True}))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.is_stop_requested("job-1") is True
    _, url, _ = fake.calls[0]
    assert url == "http://host:8787/api/worker/jobs/job-1/control"


def test_wait_and_clear_resume_reads_the_resumed_flag(fake):
    fake.queue(_FakeResponse(200, json_body={"resumed": True}))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.wait_and_clear_resume("job-1") is True


def test_set_paused_posts_index_action_variables_and_path(fake):
    fake.queue(_FakeResponse(200, json_body={"ok": True}))
    store = RemoteStore("http://host:8787", session=fake)

    store.set_paused("job-1", 2, "click", {"x": 1}, "0.then.1")

    _, url, kwargs = fake.calls[0]
    assert url == "http://host:8787/api/worker/jobs/job-1/pause"
    assert kwargs["json"] == {"index": 2, "action": "click", "variables": {"x": 1}, "path": "0.then.1"}


def test_finish_job_posts_status_and_error_message(fake):
    fake.queue(_FakeResponse(200, json_body={"ok": True}))
    store = RemoteStore("http://host:8787", session=fake)

    store.finish_job("job-1", "error", "boom")

    _, url, kwargs = fake.calls[0]
    assert url == "http://host:8787/api/worker/jobs/job-1/finish"
    assert kwargs["json"] == {"status": "error", "error_message": "boom"}


def test_get_global_variables_returns_the_dict(fake):
    fake.queue(_FakeResponse(200, json_body={"base_url": "https://intern"}))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.get_global_variables() == {"base_url": "https://intern"}


def test_get_queue_by_name_url_encodes_the_name(fake):
    fake.queue(_FakeResponse(200, json_body={"id": 3, "name": "invoices/2026"}))
    store = RemoteStore("http://host:8787", session=fake)

    queue = store.get_queue_by_name("invoices/2026")

    assert queue["id"] == 3
    _, url, _ = fake.calls[0]
    assert url == "http://host:8787/api/worker/queues/by-name?name=invoices%2F2026"


def test_get_queue_by_name_returns_none_when_missing(fake):
    fake.queue(_FakeResponse(200, json_body=None))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.get_queue_by_name("missing") is None


def test_claim_next_queue_item_posts_locked_by(fake):
    fake.queue(_FakeResponse(200, json_body={"id": 7, "payload": "{}"}))
    store = RemoteStore("http://host:8787", session=fake)

    item = store.claim_next_queue_item(3, "job-1")

    assert item["id"] == 7
    _, url, kwargs = fake.calls[0]
    assert url == "http://host:8787/api/worker/queues/3/claim"
    assert kwargs["json"] == {"locked_by": "job-1"}


def test_seconds_until_next_retry_reads_seconds(fake):
    fake.queue(_FakeResponse(200, json_body={"seconds": 12.5}))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.seconds_until_next_retry(3) == 12.5


def test_seconds_until_next_retry_can_be_none(fake):
    fake.queue(_FakeResponse(200, json_body={"seconds": None}))
    store = RemoteStore("http://host:8787", session=fake)

    assert store.seconds_until_next_retry(3) is None


def test_complete_queue_item_posts_all_fields_and_returns_status(fake):
    fake.queue(_FakeResponse(200, json_body={"status": "failed"}))
    store = RemoteStore("http://host:8787", session=fake)

    status = store.complete_queue_item(9, False, error_message="boom", permanent=True)

    assert status == "failed"
    _, url, kwargs = fake.calls[0]
    assert url == "http://host:8787/api/worker/queue_items/9/complete"
    assert kwargs["json"] == {"success": False, "output": None, "error_message": "boom", "permanent": True}


def test_release_queue_item_posts_to_the_right_url(fake):
    fake.queue(_FakeResponse(200, json_body={"ok": True}))
    store = RemoteStore("http://host:8787", session=fake)

    store.release_queue_item(9)

    _, url, _ = fake.calls[0]
    assert url == "http://host:8787/api/worker/queue_items/9/release"


def test_a_4xx_response_raises_remote_store_error(fake):
    fake.queue(_FakeResponse(403))
    store = RemoteStore("http://host:8787", session=fake)

    with pytest.raises(RemoteStoreError):
        store.get_global_variables()


def test_init_db_is_a_no_op(fake):
    # The server owns and initializes its own orchestrator.db - a remote
    # worker has nothing local to set up, unlike run_worker_loop's default
    # `store=db` case (see worker.py).
    store = RemoteStore("http://host:8787", session=fake)

    store.init_db()

    assert fake.calls == []
