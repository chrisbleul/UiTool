from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_BACKENDS = ("web", "desktop")

# Where workflows referenced *by name* are looked up - the `run_workflow` action
# and the Studio both resolve against this one directory, so a sub-workflow is
# the same file the builder saves and lists.
WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


VALID_ON_ERROR = ("continue", "retry")


@dataclass
class Step:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    breakpoint: bool = False
    save_as: str | None = None
    # Per-step error policy, independent of any enclosing `try`/`catch` (see
    # engine.py's _run_step_with_policy): None means the existing default -
    # a failure aborts the workflow. "continue" logs the failure and moves on
    # to the next step; "retry" re-attempts the same step up to `retry_count`
    # times, waiting `retry_delay` seconds between attempts, before falling
    # back to aborting.
    on_error: str | None = None
    retry_count: int = 3
    retry_delay: float = 2.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        data = dict(data)
        try:
            action = data.pop("action")
        except KeyError as exc:
            raise ValueError(f"Step is missing required 'action' key: {data}") from exc
        breakpoint_flag = bool(data.pop("breakpoint", False))
        save_as = data.pop("save_as", None) or None
        on_error = data.pop("on_error", None) or None
        if on_error is not None and on_error not in VALID_ON_ERROR:
            raise ValueError(f"Unknown on_error '{on_error}', expected one of {VALID_ON_ERROR}")
        retry_count = int(data.pop("retry_count", 3))
        retry_delay = float(data.pop("retry_delay", 2.0))
        return cls(
            action=action,
            params=data,
            breakpoint=breakpoint_flag,
            save_as=save_as,
            on_error=on_error,
            retry_count=retry_count,
            retry_delay=retry_delay,
        )


VALID_BROWSER_CHANNELS = (None, "chrome", "msedge")


@dataclass
class Workflow:
    name: str
    backend: str
    steps: list[Step]
    # Only meaningful when backend == "web": None runs Playwright's own bundled
    # Chromium build; "chrome"/"msedge" instead drive the locally installed
    # Google Chrome / Microsoft Edge (must already be installed on the machine).
    browser_channel: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Workflow":
        backend = raw.get("backend", "web")
        if backend not in VALID_BACKENDS:
            raise ValueError(f"Unknown backend '{backend}', expected one of {VALID_BACKENDS}")
        browser_channel = raw.get("browser_channel") or None
        if browser_channel not in VALID_BROWSER_CHANNELS:
            raise ValueError(f"Unknown browser_channel '{browser_channel}', expected one of {VALID_BROWSER_CHANNELS}")
        steps = [Step.from_dict(s) for s in raw.get("steps", [])]
        return cls(name=raw.get("name", "workflow"), backend=backend, steps=steps, browser_channel=browser_channel)

    @classmethod
    def load(cls, path: str | Path) -> "Workflow":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw.setdefault("name", path.stem)
        return cls.from_raw(raw)

    def to_dict(self) -> dict[str, Any]:
        steps = []
        for s in self.steps:
            entry: dict[str, Any] = {"action": s.action, **s.params}
            if s.breakpoint:
                entry["breakpoint"] = True
            if s.save_as:
                entry["save_as"] = s.save_as
            if s.on_error:
                entry["on_error"] = s.on_error
                if s.on_error == "retry":
                    entry["retry_count"] = s.retry_count
                    entry["retry_delay"] = s.retry_delay
            steps.append(entry)
        result: dict[str, Any] = {"name": self.name, "backend": self.backend, "steps": steps}
        if self.browser_channel:
            result["browser_channel"] = self.browser_channel
        return result

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def workflow_path(name: str) -> Path:
    """Resolves a workflow *name* to its file in WORKFLOWS_DIR. Any directory
    part is discarded, so a name coming from a workflow definition can't reach
    outside that directory."""
    filename = Path(name).name
    if not filename.endswith(".yaml"):
        filename += ".yaml"
    return WORKFLOWS_DIR / filename


def load_workflow_by_name(name: str) -> Workflow:
    path = workflow_path(name)
    if not path.exists():
        raise FileNotFoundError(f"No workflow named '{name}' in {WORKFLOWS_DIR}")
    return Workflow.load(path)
