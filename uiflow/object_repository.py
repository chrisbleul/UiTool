"""Persistent store for the Object Repository: named UI elements grouped by
application scope, referenced from a step as `element: "<scope>/<name>"`
instead of repeating a selector (web) or control_type/title/auto_id (desktop)
inline in every activity that targets the same element - so a UI change is
fixed in one place instead of wherever that element happened to be used.

Lives in its own file inside WORKFLOWS_DIR rather than orchestrator.db: a
selector isn't a secret, unlike a credential, and belongs to the same
version-controlled place as the workflows that reference it - otherwise a
change to the target application's UI couldn't be reviewed and rolled back
together with the workflows it affects (see README's Object Repository
section)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import WORKFLOWS_DIR

# A reserved name, not a real workflow - excluded from the Studio's workflow
# listing/run-workflow datalist (see studio/app.py's list_workflows).
REPOSITORY_FILENAME = "_object_repository.yaml"


def repository_path() -> Path:
    return WORKFLOWS_DIR / REPOSITORY_FILENAME


def load_repository() -> dict[str, dict[str, dict[str, Any]]]:
    path = repository_path()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def save_repository(data: dict[str, dict[str, dict[str, Any]]]) -> None:
    path = repository_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")


def get_element(scope: str, name: str) -> dict[str, Any] | None:
    return load_repository().get(scope, {}).get(name)


def set_element(scope: str, name: str, fields: dict[str, Any]) -> None:
    if not scope or not name:
        raise ValueError("scope and name are both required")
    data = load_repository()
    data.setdefault(scope, {})[name] = fields
    save_repository(data)


def delete_element(scope: str, name: str) -> None:
    data = load_repository()
    if name in data.get(scope, {}):
        del data[scope][name]
        if not data[scope]:
            del data[scope]
        save_repository(data)


def list_elements() -> list[dict[str, Any]]:
    """Flat {scope, name, fields} list, sorted - the shape both the Studio's
    repository panel and an "Element-Referenz" picker field render from."""
    data = load_repository()
    return [
        {"scope": scope, "name": name, "fields": fields}
        for scope in sorted(data)
        for name, fields in sorted(data[scope].items())
    ]
