"""Secret storage for the engine's `get_credential` action, backed by the OS
credential store via the `keyring` package (Windows Credential Manager /
macOS Keychain / Secret Service on Linux) rather than a hand-rolled encryption
scheme - the value never touches our own database or disk.

`keyring` has no cross-backend "list all names" API, so the orchestrator DB
separately tracks just the *names* that have been set (see
orchestrator/db.py's credentials table) - never the secret values themselves -
so the Studio can render a picker without ever reading a secret back out."""

from __future__ import annotations

_SERVICE = "uiflow"


def set_credential(name: str, value: str) -> None:
    import keyring

    keyring.set_password(_SERVICE, name, value)


def get_credential(name: str) -> str:
    import keyring

    value = keyring.get_password(_SERVICE, name)
    if value is None:
        raise KeyError(f"No credential named '{name}' is stored")
    return value


def delete_credential(name: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(_SERVICE, name)
    except PasswordDeleteError:
        pass  # already gone - deleting is idempotent from the caller's perspective
