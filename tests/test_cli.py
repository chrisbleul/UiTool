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
