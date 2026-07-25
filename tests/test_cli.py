import argparse

import pytest
from werkzeug.security import check_password_hash

from uiflow import cli
from uiflow.orchestrator import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "orchestrator.db")


def _args(username="alice", role="admin", password=None, update=False):
    return argparse.Namespace(username=username, role=role, password=password, update=update)


def test_create_user_creates_a_new_account():
    code = cli.cmd_create_user(_args(password="s3cret"))

    assert code == 0
    user = db.get_user("alice")
    assert user["role"] == "admin"
    assert check_password_hash(user["password_hash"], "s3cret")


def test_create_user_refuses_to_recreate_an_existing_user_without_update():
    cli.cmd_create_user(_args(password="s3cret"))

    code = cli.cmd_create_user(_args(password="other"))

    assert code == 1
    # the original password/role must still be intact - a refused create may not overwrite
    assert check_password_hash(db.get_user("alice")["password_hash"], "s3cret")


def test_create_user_with_update_changes_password_and_role():
    cli.cmd_create_user(_args(password="s3cret", role="viewer"))

    code = cli.cmd_create_user(_args(password="new-pw", role="operator", update=True))

    assert code == 0
    user = db.get_user("alice")
    assert user["role"] == "operator"
    assert check_password_hash(user["password_hash"], "new-pw")


def test_create_user_update_on_a_nonexistent_user_fails():
    code = cli.cmd_create_user(_args(password="pw", update=True))

    assert code == 1
    assert db.get_user("alice") is None


def test_create_user_rejects_an_empty_prompted_password(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")

    code = cli.cmd_create_user(_args(password=None))

    assert code == 1
    assert db.get_user("alice") is None


def test_create_user_rejects_mismatched_prompted_passwords(monkeypatch):
    answers = iter(["s3cret", "different"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))

    code = cli.cmd_create_user(_args(password=None))

    assert code == 1
    assert db.get_user("alice") is None


def test_create_user_accepts_matching_prompted_passwords(monkeypatch):
    answers = iter(["s3cret", "s3cret"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))

    code = cli.cmd_create_user(_args(password=None))

    assert code == 0
    assert check_password_hash(db.get_user("alice")["password_hash"], "s3cret")


def test_build_parser_accepts_create_user_command():
    parser = cli.build_parser()

    args = parser.parse_args(["create-user", "alice", "--role", "operator", "--password", "pw"])

    assert args.username == "alice"
    assert args.role == "operator"
    assert args.password == "pw"
    assert args.func is cli.cmd_create_user


def test_build_parser_accepts_worker_remote_flags():
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "worker",
            "--worker-id",
            "robot-1",
            "--remote-url",
            "http://studio-host:8787",
            "--remote-username",
            "robot",
            "--remote-password",
            "pw",
        ]
    )

    assert args.worker_id == "robot-1"
    assert args.remote_url == "http://studio-host:8787"
    assert args.remote_username == "robot"
    assert args.remote_password == "pw"
    assert args.func is cli.cmd_worker


def _worker_args(worker_id="robot-1", poll_interval=1.0, remote_url=None, remote_username=None, remote_password=None):
    return argparse.Namespace(
        worker_id=worker_id,
        poll_interval=poll_interval,
        remote_url=remote_url,
        remote_username=remote_username,
        remote_password=remote_password,
    )


def test_cmd_worker_runs_locally_without_remote_url(monkeypatch):
    calls = []
    monkeypatch.setattr("uiflow.orchestrator.worker.run_worker_loop", lambda **kwargs: calls.append(kwargs))

    code = cli.cmd_worker(_worker_args())

    assert code == 0
    assert calls == [{"worker_id": "robot-1", "poll_interval": 1.0}]


def test_cmd_worker_logs_in_and_passes_a_remote_store_when_remote_url_is_given(monkeypatch):
    login_calls = []
    run_calls = []

    def fake_login(self, password, username=None):
        login_calls.append((password, username))

    monkeypatch.setattr("uiflow.orchestrator.remote_store.RemoteStore.login", fake_login)
    monkeypatch.setattr("uiflow.orchestrator.worker.run_worker_loop", lambda **kwargs: run_calls.append(kwargs))

    code = cli.cmd_worker(
        _worker_args(remote_url="http://studio-host:8787", remote_username="robot", remote_password="s3cret")
    )

    assert code == 0
    assert login_calls == [("s3cret", "robot")]
    assert len(run_calls) == 1
    assert run_calls[0]["worker_id"] == "robot-1"
    from uiflow.orchestrator.remote_store import RemoteStore

    assert isinstance(run_calls[0]["store"], RemoteStore)


def test_cmd_worker_prompts_for_a_password_when_remote_url_is_given_without_one(monkeypatch):
    monkeypatch.setattr("uiflow.orchestrator.remote_store.RemoteStore.login", lambda self, password, username=None: None)
    monkeypatch.setattr("uiflow.orchestrator.worker.run_worker_loop", lambda **kwargs: None)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "prompted-pw")

    code = cli.cmd_worker(_worker_args(remote_url="http://studio-host:8787"))

    assert code == 0


def test_cmd_worker_aborts_when_remote_login_is_rejected(monkeypatch):
    from uiflow.orchestrator.remote_store import RemoteStoreError

    def fake_login(self, password, username=None):
        raise RemoteStoreError("nope")

    run_calls = []
    monkeypatch.setattr("uiflow.orchestrator.remote_store.RemoteStore.login", fake_login)
    monkeypatch.setattr("uiflow.orchestrator.worker.run_worker_loop", lambda **kwargs: run_calls.append(kwargs))

    code = cli.cmd_worker(
        _worker_args(remote_url="http://studio-host:8787", remote_username="robot", remote_password="wrong")
    )

    assert code == 1
    assert run_calls == []
