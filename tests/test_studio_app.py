import pytest

from uiflow.orchestrator import db
from uiflow.studio.app import create_app


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "orchestrator.db")
    db.init_db()


@pytest.fixture
def client(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr("uiflow.studio.app.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.delenv("UIFLOW_STUDIO_PASSWORD", raising=False)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def protected_client(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr("uiflow.studio.app.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setenv("UIFLOW_STUDIO_PASSWORD", "hunter2")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_no_password_set_allows_unauthenticated_access(client):
    res = client.get("/api/schema")
    assert res.status_code == 200


def test_password_set_blocks_api_without_login(protected_client):
    res = protected_client.get("/api/schema")
    assert res.status_code == 401


def test_password_set_redirects_index_to_login(protected_client):
    res = protected_client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_login_with_correct_password_grants_access(protected_client):
    res = protected_client.post("/login", data={"password": "hunter2"})
    assert res.status_code == 302
    assert res.headers["Location"] == "/"

    res = protected_client.get("/api/schema")
    assert res.status_code == 200


def test_login_with_wrong_password_is_rejected(protected_client):
    res = protected_client.post("/login", data={"password": "wrong"})
    assert "/login" in res.headers["Location"]

    res = protected_client.get("/api/schema")
    assert res.status_code == 401


def test_logout_revokes_access(protected_client):
    protected_client.post("/login", data={"password": "hunter2"})
    assert protected_client.get("/api/schema").status_code == 200

    protected_client.post("/logout")

    assert protected_client.get("/api/schema").status_code == 401


def test_login_page_itself_is_reachable_without_auth(protected_client):
    res = protected_client.get("/login")
    assert res.status_code == 200


def test_static_assets_are_reachable_without_auth(protected_client):
    res = protected_client.get("/static/style.css")
    assert res.status_code == 200


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, name, value):
        self.store[(service, name)] = value


def test_credentials_endpoint_stores_name_but_never_returns_the_value(client, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr("keyring.set_password", fake.set_password)

    res = client.post("/api/credentials", json={"name": "smtp_password", "value": "s3cr3t"})
    assert res.status_code == 200

    res = client.get("/api/credentials")
    assert res.get_json() == ["smtp_password"]
    assert fake.store[("uiflow", "smtp_password")] == "s3cr3t"


def test_credentials_endpoint_requires_name_and_value(client):
    res = client.post("/api/credentials", json={"name": "", "value": ""})
    assert res.status_code == 400


def test_delete_credential_endpoint(client, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr("keyring.set_password", fake.set_password)
    monkeypatch.setattr("keyring.delete_password", lambda service, name: fake.store.pop((service, name), None))
    client.post("/api/credentials", json={"name": "x", "value": "y"})

    res = client.delete("/api/credentials/x")

    assert res.status_code == 200
    assert client.get("/api/credentials").get_json() == []


def test_create_and_list_schedule_via_api(client):
    workflow = {"name": "demo", "backend": "web", "steps": [{"action": "navigate", "url": "https://x"}]}
    res = client.post(
        "/api/schedules", json={"name": "nightly", "cron_expr": "0 2 * * *", "workflow": workflow}
    )
    assert res.status_code == 200
    schedule_id = res.get_json()["id"]

    res = client.get("/api/schedules")
    [schedule] = res.get_json()
    assert schedule["id"] == schedule_id
    assert schedule["name"] == "nightly"
    assert "workflow_json" not in schedule


def test_create_schedule_rejects_invalid_cron(client):
    workflow = {"name": "demo", "backend": "web", "steps": []}
    res = client.post("/api/schedules", json={"name": "bad", "cron_expr": "not-a-cron", "workflow": workflow})
    assert res.status_code == 400


def test_toggle_and_delete_schedule_via_api(client):
    workflow = {"name": "demo", "backend": "web", "steps": []}
    res = client.post("/api/schedules", json={"name": "nightly", "cron_expr": "0 2 * * *", "workflow": workflow})
    schedule_id = res.get_json()["id"]

    res = client.post(f"/api/schedules/{schedule_id}/toggle")
    assert res.get_json() == {"enabled": False}

    res = client.delete(f"/api/schedules/{schedule_id}")
    assert res.status_code == 200
    assert client.get("/api/schedules").get_json() == []
