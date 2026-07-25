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


@pytest.fixture
def multiuser_app(isolated_db, monkeypatch, tmp_path):
    from werkzeug.security import generate_password_hash

    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setattr("uiflow.object_repository.WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.delenv("UIFLOW_STUDIO_PASSWORD", raising=False)
    db.create_user("admin1", generate_password_hash("adminpass"), "admin")
    db.create_user("op1", generate_password_hash("oppass"), "operator")
    db.create_user("view1", generate_password_hash("viewpass"), "viewer")
    app = create_app()
    app.config.update(TESTING=True)
    return app


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


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


# --- multi-user RBAC (see db.any_users_exist / studio/app.py's require_login) ---


def test_multiuser_mode_blocks_unauthenticated_api_access(multiuser_app):
    client = multiuser_app.test_client()

    res = client.get("/api/schema")

    assert res.status_code == 401


def test_multiuser_login_sets_username_and_role(multiuser_app):
    client = multiuser_app.test_client()

    res = _login(client, "admin1", "adminpass")

    assert res.status_code == 302 and res.headers["Location"] == "/"
    me = client.get("/api/me").get_json()
    assert me == {"username": "admin1", "role": "admin", "multiuser": True}


def test_multiuser_login_with_wrong_password_is_rejected(multiuser_app):
    client = multiuser_app.test_client()

    res = _login(client, "admin1", "wrong")

    assert "/login" in res.headers["Location"]
    assert client.get("/api/schema").status_code == 401


def test_api_me_reports_logged_out_state_in_multiuser_mode(multiuser_app):
    client = multiuser_app.test_client()

    assert client.get("/api/me").get_json() == {"username": None, "role": None, "multiuser": True}


def test_api_me_reports_admin_in_single_user_mode(client):
    assert client.get("/api/me").get_json() == {"username": None, "role": "admin", "multiuser": False}


def test_viewer_can_read_but_not_write(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "view1", "viewpass")

    assert client.get("/api/workflows").status_code == 200
    res = client.post("/api/workflows/x", json=_workflow("x", "https://a"))
    assert res.status_code == 403


def test_operator_can_write_workflows_but_not_manage_users_or_credentials(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "op1", "oppass")

    assert client.post("/api/workflows/x", json=_workflow("x", "https://a")).status_code == 200
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/credentials", json={"name": "x", "value": "y"}).status_code == 403
    assert client.post("/api/globals", json={"name": "x", "value": "1"}).status_code == 403


def test_audit_log_endpoint_requires_admin_in_multiuser_mode(multiuser_app):
    viewer = multiuser_app.test_client()
    _login(viewer, "view1", "viewpass")
    assert viewer.get("/api/audit-log").status_code == 403

    operator = multiuser_app.test_client()
    _login(operator, "op1", "oppass")
    assert operator.get("/api/audit-log").status_code == 403

    admin = multiuser_app.test_client()
    _login(admin, "admin1", "adminpass")
    assert admin.get("/api/audit-log").status_code == 200


def test_audit_log_records_the_acting_username_and_role(multiuser_app):
    admin = multiuser_app.test_client()
    _login(admin, "admin1", "adminpass")

    admin.post("/api/globals", json={"name": "x", "value": "1"})

    entries = admin.get("/api/audit-log").get_json()
    entry = next(e for e in entries if e["action"] == "POST /api/globals")
    assert entry["username"] == "admin1"
    assert entry["role"] == "admin"
    assert entry["status_code"] == 200


def test_admin_can_manage_users(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.post("/api/users", json={"username": "new1", "password": "pw", "role": "viewer"})
    assert res.status_code == 200

    names = {u["username"] for u in client.get("/api/users").get_json()}
    assert names == {"admin1", "op1", "view1", "new1"}

    res = client.delete("/api/users/new1")
    assert res.status_code == 200
    names = {u["username"] for u in client.get("/api/users").get_json()}
    assert "new1" not in names


def test_new_user_can_log_in_with_the_password_set_by_admin(multiuser_app):
    admin_client = multiuser_app.test_client()
    _login(admin_client, "admin1", "adminpass")
    admin_client.post("/api/users", json={"username": "new1", "password": "pw", "role": "operator"})

    new_client = multiuser_app.test_client()
    _login(new_client, "new1", "pw")

    assert new_client.get("/api/me").get_json()["role"] == "operator"


def test_admin_cannot_demote_their_own_account(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.patch("/api/users/admin1", json={"role": "viewer"})

    assert res.status_code == 400
    assert client.get("/api/me").get_json()["role"] == "admin"


def test_admin_cannot_delete_their_own_account(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.delete("/api/users/admin1")

    assert res.status_code == 400
    assert any(u["username"] == "admin1" for u in client.get("/api/users").get_json())


def test_admin_can_update_another_users_role(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.patch("/api/users/view1", json={"role": "operator"})

    assert res.status_code == 200
    roles = {u["username"]: u["role"] for u in client.get("/api/users").get_json()}
    assert roles["view1"] == "operator"


def test_create_user_endpoint_rejects_a_duplicate_username(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.post("/api/users", json={"username": "op1", "password": "pw", "role": "viewer"})

    assert res.status_code == 409


def test_create_user_endpoint_rejects_an_unknown_role(multiuser_app):
    client = multiuser_app.test_client()
    _login(client, "admin1", "adminpass")

    res = client.post("/api/users", json={"username": "new2", "password": "pw", "role": "superadmin"})

    assert res.status_code == 400


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


def test_worker_claim_endpoint_claims_the_oldest_queued_job(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    res = client.post("/api/worker/claim", json={"worker_id": "remote-1"})

    assert res.status_code == 200
    claimed = res.get_json()
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["worker_id"] == "remote-1"


def test_worker_claim_endpoint_returns_null_when_nothing_queued(client):
    res = client.post("/api/worker/claim", json={"worker_id": "remote-1"})

    assert res.status_code == 200
    assert res.get_json() is None


def test_worker_add_log_endpoint_persists_a_log_line(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    res = client.post(f"/api/worker/jobs/{job_id}/logs", json={"level": "INFO", "message": "hallo"})

    assert res.status_code == 200
    logs = client.get(f"/api/jobs/{job_id}/logs").get_json()
    assert any(log["message"] == "hallo" for log in logs)


def test_worker_job_control_endpoint_reports_stop_requested(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    assert client.get(f"/api/worker/jobs/{job_id}/control").get_json() == {"stop_requested": False}

    client.post(f"/api/run/{job_id}/stop")

    assert client.get(f"/api/worker/jobs/{job_id}/control").get_json() == {"stop_requested": True}


def test_worker_job_resume_clear_endpoint_consumes_the_resume_flag(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]
    db.request_resume(job_id)

    res = client.post(f"/api/worker/jobs/{job_id}/resume_clear")
    assert res.get_json() == {"resumed": True}

    # a second call finds nothing left to clear - the flag was consumed
    res = client.post(f"/api/worker/jobs/{job_id}/resume_clear")
    assert res.get_json() == {"resumed": False}


def test_worker_job_pause_endpoint_stores_breakpoint_state(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    res = client.post(
        f"/api/worker/jobs/{job_id}/pause",
        json={"index": 1, "action": "click", "variables": {"x": 1}, "path": "1"},
    )

    assert res.status_code == 200
    controls = db.get_controls(job_id)
    assert controls["paused_step_index"] == 1
    assert controls["paused_step_action"] == "click"


def test_worker_job_finish_endpoint_marks_the_job_done(client):
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    res = client.post(f"/api/worker/jobs/{job_id}/finish", json={"status": "error", "error_message": "boom"})

    assert res.status_code == 200
    detail = client.get(f"/api/jobs/{job_id}").get_json()
    assert detail["status"] == "error"
    assert detail["error_message"] == "boom"


def test_worker_job_finish_endpoint_notifies_on_error_for_a_remote_worker(client, monkeypatch):
    # A remote worker's own RemoteStore.notify_job_failed is a no-op (see its
    # docstring) - the server has to do this itself when it handles a remote
    # worker's finish call, which is exactly what this endpoint is.
    captured = {}
    monkeypatch.setattr("uiflow.email_client.send_email", lambda **kwargs: captured.update(kwargs))
    client.post(
        "/api/notifications",
        json={"enabled": True, "smtp_host": "smtp.example.com", "smtp_port": 587, "to_addr": "ops@example.com"},
    )
    job_id = client.post("/api/run", json=_workflow("x", "https://a")).get_json()["job_id"]

    client.post(f"/api/worker/jobs/{job_id}/finish", json={"status": "error", "error_message": "boom"})

    assert captured["to"] == "ops@example.com"
    assert "boom" in captured["body"]


def test_worker_globals_endpoint_matches_the_studio_globals(client):
    client.post("/api/globals", json={"name": "basis_url", "value": "https://intern"})

    res = client.get("/api/worker/globals")

    assert res.get_json() == {"basis_url": "https://intern"}


def test_worker_queue_by_name_endpoint_returns_the_queue_or_null(client):
    client.post("/api/queues", json={"name": "rechnungen"})

    found = client.get("/api/worker/queues/by-name?name=rechnungen").get_json()
    assert found["name"] == "rechnungen"

    missing = client.get("/api/worker/queues/by-name?name=nope").get_json()
    assert missing is None


def test_worker_queue_claim_process_and_release_roundtrip(client):
    queue_id = client.post("/api/queues", json={"name": "rechnungen"}).get_json()["id"]
    client.post("/api/queues/rechnungen/items", json={"items": [{"payload": {"betrag": 42}}]})

    item = client.post(f"/api/worker/queues/{queue_id}/claim", json={"locked_by": "job-1"}).get_json()
    assert item["payload"] == '{"betrag": 42}'

    # nothing else left to claim right now, and nothing awaiting a retry either
    assert client.post(f"/api/worker/queues/{queue_id}/claim", json={"locked_by": "job-1"}).get_json() is None
    assert client.get(f"/api/worker/queues/{queue_id}/next_retry_wait").get_json() == {"seconds": None}

    res = client.post(f"/api/worker/queue_items/{item['id']}/complete", json={"success": True, "output": {}})
    assert res.get_json() == {"status": "success"}


def test_worker_queue_item_release_endpoint_hands_it_back_unprocessed(client):
    queue_id = client.post("/api/queues", json={"name": "rechnungen"}).get_json()["id"]
    client.post("/api/queues/rechnungen/items", json={"items": [{"payload": {}}]})
    item = client.post(f"/api/worker/queues/{queue_id}/claim", json={"locked_by": "job-1"}).get_json()

    res = client.post(f"/api/worker/queue_items/{item['id']}/release")

    assert res.status_code == 200
    items = client.get("/api/queues/rechnungen/items").get_json()
    assert items[0]["status"] == "new"


def test_worker_queue_item_complete_endpoint_marks_a_business_error_permanent(client):
    queue_id = client.post("/api/queues", json={"name": "rechnungen"}).get_json()["id"]
    client.post("/api/queues/rechnungen/items", json={"items": [{"payload": {}}]})
    item = client.post(f"/api/worker/queues/{queue_id}/claim", json={"locked_by": "job-1"}).get_json()

    res = client.post(
        f"/api/worker/queue_items/{item['id']}/complete",
        json={"success": False, "error_message": "ungueltig", "permanent": True},
    )

    assert res.get_json() == {"status": "failed"}
    items = client.get("/api/queues/rechnungen/items").get_json()
    assert items[0]["status"] == "failed"
    assert items[0]["retry_count"] == 0  # a permanent failure must not consume a retry


def test_worker_api_requires_at_least_operator_in_multiuser_mode(multiuser_app):
    viewer = multiuser_app.test_client()
    _login(viewer, "view1", "viewpass")
    assert viewer.post("/api/worker/claim", json={"worker_id": "w"}).status_code == 403

    operator = multiuser_app.test_client()
    _login(operator, "op1", "oppass")
    assert operator.post("/api/worker/claim", json={"worker_id": "w"}).status_code == 200


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


def test_inspect_desktop_endpoint_reports_match_count(client, monkeypatch):
    captured = {}

    def fake_inspect(**kwargs):
        captured.update(kwargs)
        return {"count": 1, "matches": [{"control_type": "Button", "title": "OK", "auto_id": "btnOK"}]}

    monkeypatch.setattr("uiflow.studio.picker.inspect_desktop_selector", fake_inspect)

    res = client.post(
        "/api/inspect/desktop",
        json={"focus_title": "Editor", "selector": {"control_type": "Button", "title": ""}},
    )

    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] == 1
    assert captured == {"focus_title": "Editor", "focus_path": None, "control_type": "Button"}


def test_inspect_desktop_endpoint_requires_a_focus_title_or_path(client):
    res = client.post("/api/inspect/desktop", json={"selector": {"control_type": "Button"}})

    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_inspect_desktop_endpoint_reports_a_value_error_as_a_400(client, monkeypatch):
    def raise_value_error(**kwargs):
        raise ValueError("Anwendung nicht erreichbar")

    monkeypatch.setattr("uiflow.studio.picker.inspect_desktop_selector", raise_value_error)

    res = client.post("/api/inspect/desktop", json={"focus_title": "Editor", "selector": {}})

    assert res.status_code == 400
    assert "Anwendung nicht erreichbar" in res.get_json()["error"]


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


def test_first_save_of_a_new_workflow_creates_no_version(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://original"))

    assert client.get("/api/workflows/report/versions").get_json() == []


def test_overwriting_a_workflow_archives_the_previous_content_as_a_version(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://original"))
    client.post("/api/workflows/report", json=_workflow("report", "https://updated"))

    versions = client.get("/api/workflows/report/versions").get_json()
    assert len(versions) == 1
    version = client.get(f"/api/workflows/report/versions/{versions[0]['id']}").get_json()
    assert "https://original" in version["content_yaml"]
    # the live file is the new content, not duplicated into the version list
    assert client.get("/api/workflows/report").get_json()["steps"][0]["url"] == "https://updated"


def test_multiple_saves_produce_newest_first_version_history(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://v1"))
    client.post("/api/workflows/report", json=_workflow("report", "https://v2"))
    client.post("/api/workflows/report", json=_workflow("report", "https://v3"))

    versions = client.get("/api/workflows/report/versions").get_json()
    assert len(versions) == 2  # v1 and v2 were archived, v3 is the live file
    contents = [client.get(f"/api/workflows/report/versions/{v['id']}").get_json()["content_yaml"] for v in versions]
    assert "https://v2" in contents[0]  # newest archived version first
    assert "https://v1" in contents[1]


def test_restoring_a_version_writes_it_back_and_archives_the_pre_restore_state(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://v1"))
    client.post("/api/workflows/report", json=_workflow("report", "https://v2"))
    [only_version] = client.get("/api/workflows/report/versions").get_json()

    res = client.post(f"/api/workflows/report/versions/{only_version['id']}/restore")

    assert res.status_code == 200
    assert client.get("/api/workflows/report").get_json()["steps"][0]["url"] == "https://v1"
    # the state right before the restore (v2) must not be lost either
    versions = client.get("/api/workflows/report/versions").get_json()
    assert len(versions) == 2
    contents = [client.get(f"/api/workflows/report/versions/{v['id']}").get_json()["content_yaml"] for v in versions]
    assert any("https://v2" in c for c in contents)


def test_restore_endpoint_rejects_a_version_belonging_to_a_different_workflow(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://v1"))
    client.post("/api/workflows/report", json=_workflow("report", "https://v2"))
    [version] = client.get("/api/workflows/report/versions").get_json()
    client.post("/api/workflows/other", json=_workflow("other", "https://x"))

    res = client.post(f"/api/workflows/other/versions/{version['id']}/restore")

    assert res.status_code == 404


def test_deleting_a_workflow_also_deletes_its_version_history(client):
    client.post("/api/workflows/report", json=_workflow("report", "https://v1"))
    client.post("/api/workflows/report", json=_workflow("report", "https://v2"))

    client.delete("/api/workflows/report")

    # the file is gone, so a version list for it must not error - it's just empty
    assert client.get("/api/workflows/report/versions").get_json() == []


def test_workflow_version_records_the_acting_username(multiuser_app):
    admin = multiuser_app.test_client()
    _login(admin, "admin1", "adminpass")
    admin.post("/api/workflows/report", json=_workflow("report", "https://v1"))
    admin.post("/api/workflows/report", json=_workflow("report", "https://v2"))

    [version] = admin.get("/api/workflows/report/versions").get_json()

    assert version["saved_by"] == "admin1"


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


def test_notifications_endpoint_defaults_to_disabled(client):
    settings = client.get("/api/notifications").get_json()

    assert settings["enabled"] is False
    assert settings["smtp_host"] is None


def test_notifications_endpoint_stores_settings(client):
    res = client.post(
        "/api/notifications",
        json={
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "use_tls": False,
            "username": "bot@example.com",
            "from_addr": "bot@example.com",
            "to_addr": "ops@example.com",
            "credential_name": "smtp_password",
        },
    )

    assert res.status_code == 200
    settings = client.get("/api/notifications").get_json()
    assert settings["enabled"] is True
    assert settings["smtp_host"] == "smtp.example.com"
    assert settings["smtp_port"] == 465
    assert settings["to_addr"] == "ops@example.com"


def test_notifications_test_endpoint_reports_a_clear_error_when_not_configured(client):
    res = client.post("/api/notifications/test")

    assert res.status_code == 400
    assert "error" in res.get_json()


def test_notifications_test_endpoint_sends_when_configured(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("uiflow.email_client.send_email", lambda **kwargs: captured.update(kwargs))
    client.post(
        "/api/notifications",
        json={
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "use_tls": True,
            "to_addr": "ops@example.com",
        },
    )

    res = client.post("/api/notifications/test")

    assert res.status_code == 200
    assert res.get_json() == {"sent": True}
    assert captured["to"] == "ops@example.com"
    assert "Test" in captured["subject"]


def test_notifications_endpoint_requires_admin_in_multiuser_mode(multiuser_app):
    operator = multiuser_app.test_client()
    _login(operator, "op1", "oppass")
    assert operator.get("/api/notifications").status_code == 403
    assert operator.post("/api/notifications", json={}).status_code == 403

    admin = multiuser_app.test_client()
    _login(admin, "admin1", "adminpass")
    assert admin.get("/api/notifications").status_code == 200


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


def test_audit_log_is_reachable_without_admin_outside_multiuser_mode(client):
    # _required_role's admin gate is only consulted once db.any_users_exist() -
    # single-user mode has no accounts to gate by, same as credentials/globals.
    assert client.get("/api/audit-log").status_code == 200


def test_audit_log_records_a_successful_write_with_a_null_identity_in_single_user_mode(client):
    client.post("/api/globals", json={"name": "x", "value": "1"})

    entries = client.get("/api/audit-log").get_json()
    entry = next(e for e in entries if e["action"] == "POST /api/globals")
    assert entry["username"] is None
    assert entry["role"] is None
    assert entry["status_code"] == 200


def test_audit_log_records_a_failed_attempt_too(client):
    client.post("/api/globals", json={"name": "", "value": "x"})  # rejected, 400

    entries = client.get("/api/audit-log").get_json()
    entry = next(e for e in entries if e["action"] == "POST /api/globals")
    assert entry["status_code"] == 400


def test_audit_log_does_not_record_plain_reads(client):
    client.get("/api/workflows")

    entries = client.get("/api/audit-log").get_json()
    assert not any(e["action"] == "GET /api/workflows" for e in entries)


def test_audit_log_does_not_record_worker_api_traffic(client):
    client.post("/api/worker/claim", json={"worker_id": "w1"})

    entries = client.get("/api/audit-log").get_json()
    assert not any("/api/worker/" in e["action"] for e in entries)


def test_audit_log_newest_entries_come_first(client):
    client.post("/api/globals", json={"name": "a", "value": "1"})
    client.post("/api/globals", json={"name": "b", "value": "2"})

    entries = [e for e in client.get("/api/audit-log").get_json() if e["action"] == "POST /api/globals"]
    assert len(entries) >= 2
    assert entries[0]["id"] > entries[1]["id"]


def test_globals_endpoint_refuses_a_reserved_namespace_name(client):
    """`{global.global}` would resolve against the namespace rather than the
    value, so such an entry could never be read back."""
    for name in ("global", "item", "var"):
        assert client.post("/api/globals", json={"name": name, "value": "x"}).status_code == 400


def test_delete_global_endpoint(client):
    client.post("/api/globals", json={"name": "x", "value": "1"})

    assert client.delete("/api/globals/x").status_code == 200
    assert client.get("/api/globals").get_json() == []
