import pytest

from uiflow.credentials import delete_credential, get_credential, set_credential


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def get_password(self, service, name):
        return self.store.get((service, name))

    def delete_password(self, service, name):
        from keyring.errors import PasswordDeleteError

        if (service, name) not in self.store:
            raise PasswordDeleteError()
        del self.store[(service, name)]


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr("keyring.set_password", fake.set_password)
    monkeypatch.setattr("keyring.get_password", fake.get_password)
    monkeypatch.setattr("keyring.delete_password", fake.delete_password)
    return fake


def test_set_and_get_credential_round_trips(fake_keyring):
    set_credential("smtp_password", "hunter2")

    assert get_credential("smtp_password") == "hunter2"


def test_get_credential_missing_raises_key_error(fake_keyring):
    with pytest.raises(KeyError):
        get_credential("does_not_exist")


def test_delete_credential_is_idempotent(fake_keyring):
    set_credential("x", "y")

    delete_credential("x")
    delete_credential("x")  # must not raise the second time

    with pytest.raises(KeyError):
        get_credential("x")
