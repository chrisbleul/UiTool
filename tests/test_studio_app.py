import pytest

from uiflow.orchestrator import db
from uiflow.studio.app import create_app


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "orchestrator.db")
    db.init_db()


@pytest.fixture
def client(isolated_db, monkeypatch, tmp_path):
    # one resolver for the Studio, the engine's run_workflow action, and the object repository
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setattr("uiflow.object_repository.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.delenv("UIFLOW_STUDIO_PASSWORD", raising=False)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def protected_client(isolated_db, monkeypatch, tmp_path):
    # one resolver for the Studio, the engine's run_workflow action, and the object repository
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setattr("uiflow.object_repository.WORKFLOWS_DIR", tmp_path / "workflows")
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


def _workflow(name: str, url: str) -> dict:
    return {"name": name, "backend": "web", "steps": [{"action": "navigate", "url": url}]}


def test_api_run_snapshots_referenced_sub_workflows_into_the_job(client):
    client.post(
        "/api/workflows/teilprozess",
        json={"name": "teilprozess", "backend": "web", "steps": [{"action": "navigate", "url": "sub"}]},
    )
    haupt = {
        "name": "haupt",
        "backend": "web",
        "steps": [{"action": "run_workflow", "workflow": "teilprozess"}],
    }

    res = client.post("/api/run", json=haupt)
    job_id = res.get_json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}").get_json()
    assert detail["sub_workflows"]["teilprozess"]["steps"] == [{"action": "navigate", "url": "sub"}]


def test_inspect_web_endpoint_reports_match_count(client, monkeypatch):
    monkeypatch.setattr(
        "uiflow.studio.picker.inspect_web_selector",
        lambda url, selector: {"count": 2, "matches": [{"tag": "button", "text": "Absenden", "visible": True}]},
    )

    res = client.post("/api/inspect/web", json={"url": "https://example.com", "selector": "button"})

    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] == 2


def test_inspect_web_endpoint_requires_url_and_selector(client):
    res = client.post("/api/inspect/web", json={"url": "https://example.com"})

    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_inspect_web_endpoint_reports_a_value_error_as_a_400(client, monkeypatch):
    def raise_value_error(url, selector):
        raise ValueError("Ungültiger Selector: boom")

    monkeypatch.setattr("uiflow.studio.picker.inspect_web_selector", raise_value_error)

    res = client.post("/api/inspect/web", json={"url": "https://example.com", "selector": "$bad"})

    assert res.status_code == 400
    assert "Ungültiger Selector" in res.get_json()["error"]


def test_repository_endpoints_add_list_and_delete_an_element(client):
    res = client.post(
        "/api/repository",
        json={"scope": "MeineApp", "name": "Suchfeld", "fields": {"selector": "#search"}},
    )
    assert res.status_code == 200

    entries = client.get("/api/repository").get_json()
    assert entries == [
        {
            "scope": "MeineApp",
            "name": "Suchfeld",
            "fields": {"selector": "#search"},
            "candidates": [{"selector": "#search"}],
        }
    ]

    res = client.delete("/api/repository/MeineApp/Suchfeld")
    assert res.status_code == 200
    assert client.get("/api/repository").get_json() == []


def test_repository_fallback_endpoint_appends_a_candidate(client):
    client.post("/api/repository", json={"scope": "MeineApp", "name": "Suchfeld", "fields": {"selector": "#a"}})

    res = client.post("/api/repository/MeineApp/Suchfeld/fallback", json={"fields": {"selector": "#b"}})

    assert res.status_code == 200
    assert res.get_json()["candidates"] == [{"selector": "#a"}, {"selector": "#b"}]
    entries = client.get("/api/repository").get_json()
    assert entries[0]["candidates"] == [{"selector": "#a"}, {"selector": "#b"}]


def test_repository_fallback_endpoint_requires_non_empty_fields(client):
    client.post("/api/repository", json={"scope": "MeineApp", "name": "Suchfeld", "fields": {"selector": "#a"}})

    res = client.post("/api/repository/MeineApp/Suchfeld/fallback", json={"fields": {}})

    assert res.status_code == 400


def test_repository_endpoint_requires_scope_name_and_fields(client):
    res = client.post("/api/repository", json={"scope": "MeineApp", "name": "", "fields": {"selector": "#x"}})
    assert res.status_code == 400

    res = client.post("/api/repository", json={"scope": "MeineApp", "name": "Feld", "fields": {}})
    assert res.status_code == 400


def test_repository_file_is_excluded_from_the_workflow_list(client):
    client.post("/api/repository", json={"scope": "A", "name": "B", "fields": {"selector": "#x"}})
    client.post("/api/workflows/echt", json={"name": "echt", "backend": "web", "steps": []})

    names = client.get("/api/workflows").get_json()

    assert names == ["echt"]


def test_saving_over_an_existing_workflow_is_refused_when_overwrite_is_false(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://original"))

    res = client.post("/api/workflows/report?overwrite=false", json=_workflow("report", "https://other"))

    assert res.status_code == 409
    # the original must still be intact - a refused save may not have written
    assert client.get("/api/workflows/report").get_json()["steps"][0]["url"] == "https://original"


def test_saving_over_an_existing_workflow_is_allowed_by_default(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://original"))

    res = client.post("/api/workflows/report", json=_workflow("report", "https://updated"))

    assert res.status_code == 200
    assert client.get("/api/workflows/report").get_json()["steps"][0]["url"] == "https://updated"


def test_overwrite_false_still_creates_a_workflow_that_does_not_exist(client):
    res = client.post("/api/workflows/fresh?overwrite=false", json=_workflow("fresh", "https://x"))

    assert res.status_code == 200
    assert client.get("/api/workflows/fresh").status_code == 200


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


def test_activities_endpoint_lists_every_action_with_catalog_metadata(client):
    from uiflow.studio.schema import ACTION_SCHEMAS

    payload = client.get("/api/activities").get_json()

    assert payload["categories"], "the palette needs an explicit category order"
    for backend, actions in ACTION_SCHEMAS.items():
        entries = payload["activities"][backend]
        assert {e["name"] for e in entries} == set(actions)
        for entry in entries:
            assert entry["label"] and entry["category"], entry


def test_every_action_has_a_catalog_entry():
    """A new action added to ACTION_SCHEMAS without ACTION_META would still show
    up in the palette, but as a bare action name in "Weitere" - catch that here
    rather than in the UI."""
    from uiflow.studio.schema import ACTION_META, ACTION_SCHEMAS, CATEGORY_ORDER

    known = {name for actions in ACTION_SCHEMAS.values() for name in actions}
    assert known - set(ACTION_META) == set()
    assert {meta["category"] for meta in ACTION_META.values()} <= set(CATEGORY_ORDER)


def test_catalog_primary_fields_exist_on_every_backend(client):
    """`primary` drives an activity card's one-line summary, so at least one of
    its candidates has to exist for each backend that offers the action -
    otherwise the card silently falls back to an arbitrary parameter."""
    from uiflow.studio.schema import ACTION_META, ACTION_SCHEMAS

    for backend, actions in ACTION_SCHEMAS.items():
        for name, fields in actions.items():
            candidates = ACTION_META.get(name, {}).get("primary") or []
            if not candidates:
                continue
            available = {f["name"] for f in fields}
            assert available & set(candidates), f"{backend}/{name}: none of {candidates} in {sorted(available)}"


def test_globals_endpoint_stores_and_lists_values(client):
    assert client.post("/api/globals", json={"name": "basis_url", "value": "https://x"}).status_code == 200

    [entry] = client.get("/api/globals").get_json()
    assert entry["name"] == "basis_url"
    assert entry["value"] == "https://x"


def test_globals_endpoint_parses_json_values_but_keeps_plain_text(client):
    client.post("/api/globals", json={"name": "max_betrag", "value": "5000"})
    client.post("/api/globals", json={"name": "empfaenger", "value": '["a@x.de", "b@x.de"]'})
    client.post("/api/globals", json={"name": "basis_url", "value": "https://erp.example.com"})

    values = {e["name"]: e["value"] for e in client.get("/api/globals").get_json()}
    assert values == {
        "max_betrag": 5000,
        "empfaenger": ["a@x.de", "b@x.de"],
        "basis_url": "https://erp.example.com",  # not valid JSON, so kept as text
    }


def test_globals_endpoint_requires_a_name(client):
    assert client.post("/api/globals", json={"name": "", "value": "x"}).status_code == 400


def test_globals_endpoint_refuses_a_reserved_namespace_name(client):
    """`{global.global}` would resolve against the namespace rather than the
    value, so such an entry could never be read back."""
    for name in ("global", "item", "var"):
        assert client.post("/api/globals", json={"name": name, "value": "x"}).status_code == 400


def test_delete_global_endpoint(client):
    client.post("/api/globals", json={"name": "x", "value": "1"})

    assert client.delete("/api/globals/x").status_code == 200
    assert client.get("/api/globals").get_json() == []
