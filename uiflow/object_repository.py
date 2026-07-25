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


def _as_candidate_list(value: Any) -> list[dict[str, Any]]:
    """Normalizes a stored entry to a list of candidate field-sets. A plain
    dict - the original, fallback-less format this module shipped with - is
    treated as a single-candidate list, so a repository file written before
    fallbacks existed keeps working unchanged."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def get_element(scope: str, name: str) -> dict[str, Any] | None:
    """The element's primary (first) candidate - what a step gets when its
    fields are resolved without a backend able to check which fallback
    actually matches (see engine.py's _resolve_element_reference)."""
    candidates = get_element_candidates(scope, name)
    return candidates[0] if candidates else None


def get_element_candidates(scope: str, name: str) -> list[dict[str, Any]]:
    """All of the element's alternative field-sets, in the order they should
    be tried at run time - the first one still resolves an element saved
    before fallbacks existed."""
    return _as_candidate_list(load_repository().get(scope, {}).get(name))


def set_element(scope: str, name: str, fields: dict[str, Any]) -> None:
    """Replaces the element with a single candidate (no fallbacks) - used by
    the Studio's "save current fields as element" action. Use add_fallback to
    add an alternative to an existing element instead of overwriting it."""
    if not scope or not name:
        raise ValueError("scope and name are both required")
    data = load_repository()
    data.setdefault(scope, {})[name] = fields
    save_repository(data)


def add_fallback(scope: str, name: str, fields: dict[str, Any]) -> None:
    """Appends `fields` as another candidate for an existing element, tried
    only once every earlier candidate has been checked and none matched - the
    fallback strategy a flaky/legacy-app selector needs, without every
    activity that targets the element having to carry its own fallback logic."""
    if not scope or not name:
        raise ValueError("scope and name are both required")
    data = load_repository()
    candidates = _as_candidate_list(data.get(scope, {}).get(name))
    candidates.append(fields)
    data.setdefault(scope, {})[name] = candidates
    save_repository(data)


def remove_candidate(scope: str, name: str, index: int) -> None:
    """Removes one fallback candidate by its position; removes the whole
    element if that was its last remaining candidate."""
    data = load_repository()
    candidates = _as_candidate_list(data.get(scope, {}).get(name))
    if index < 0 or index >= len(candidates):
        return
    candidates.pop(index)
    if candidates:
        data[scope][name] = candidates
    else:
        del data[scope][name]
        if not data[scope]:
            del data[scope]
    save_repository(data)


def delete_element(scope: str, name: str) -> None:
    data = load_repository()
    if name in data.get(scope, {}):
        del data[scope][name]
        if not data[scope]:
            del data[scope]
        save_repository(data)


def list_elements() -> list[dict[str, Any]]:
    """Flat list, sorted - the shape both the Studio's repository panel and an
    "Element-Referenz" picker field render from. `fields` is the primary
    (first) candidate, kept for callers that only care about one field-set;
    `candidates` is the full ordered fallback list."""
    data = load_repository()
    entries = []
    for scope in sorted(data):
        for name, raw in sorted(data[scope].items()):
            candidates = _as_candidate_list(raw)
            entries.append(
                {
                    "scope": scope,
                    "name": name,
                    "fields": candidates[0] if candidates else {},
                    "candidates": candidates,
                }
            )
    return entries
