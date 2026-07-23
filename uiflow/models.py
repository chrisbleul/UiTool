from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_BACKENDS = ("web", "desktop")


@dataclass
class Step:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    breakpoint: bool = False
    save_as: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        data = dict(data)
        try:
            action = data.pop("action")
        except KeyError as exc:
            raise ValueError(f"Step is missing required 'action' key: {data}") from exc
        breakpoint_flag = bool(data.pop("breakpoint", False))
        save_as = data.pop("save_as", None) or None
        return cls(action=action, params=data, breakpoint=breakpoint_flag, save_as=save_as)


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
