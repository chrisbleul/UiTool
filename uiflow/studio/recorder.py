"""Recording mode: watches real clicks/typing against the scope application and
turns them into workflow steps live.

Threading follows the same rules learned the hard way in picker.py: pynput's
mouse/keyboard hook callbacks must stay trivial and return fast (Windows
silently disables a low-level hook that doesn't), so they only push raw events
onto a queue. All the actual (slower, UI-Automation-heavy) resolution happens
in a separate worker thread that drains that queue - never inside a hook
callback, and never touching UI Automation concurrently *with* a hook thread
doing the same (that combination reliably broke click detection during
development)."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from .picker import _most_specific_element_at

_COMMIT_KEY_NAMES = {"enter", "tab"}


class Recorder:
    def __init__(self) -> None:
        self.events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._raw: "queue.Queue[tuple]" = queue.Queue()
        self._stop_event = threading.Event()
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._worker: threading.Thread | None = None
        self._scope_pid: int | None = None

    def start(self, focus_title: str | None, focus_path: str | None) -> None:
        from pynput import keyboard, mouse
        from pywinauto import Application

        app = (
            Application(backend="uia").connect(title=focus_title, timeout=5)
            if focus_title
            else Application(backend="uia").connect(path=Path(focus_path).name, timeout=5)
        )
        window = app.top_window()
        window.set_focus()
        self._scope_pid = window.process_id()

        def on_click(x, y, button, pressed):
            if not pressed:  # act on release, once the click has landed
                self._raw.put(("click", x, y))
            return not self._stop_event.is_set()

        def on_press(key):
            self._raw.put(("key", key))
            return not self._stop_event.is_set()

        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._keyboard_listener = keyboard.Listener(on_press=on_press)
        self._mouse_listener.start()
        self._keyboard_listener.start()

        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
        self._raw.put(("__stop__",))
        if self._worker is not None:
            self._worker.join(timeout=2)
        self.events.put({"__stopped__": True})

    def _process_loop(self) -> None:
        pending_text: list[str] = []
        last_target: dict[str, str] | None = None

        def flush_text() -> None:
            nonlocal pending_text
            if pending_text and last_target:
                self.events.put(
                    {"action": "type", "params": {**last_target, "text": "".join(pending_text)}}
                )
            pending_text = []

        while True:
            item = self._raw.get()
            if item[0] == "__stop__":
                flush_text()
                return

            if item[0] == "click":
                _, x, y = item
                try:
                    el = _most_specific_element_at(x, y)
                    if el.top_level_parent().process_id() != self._scope_pid:
                        continue  # click landed outside the scope application - ignore
                    flush_text()
                    info = el.element_info
                    target = {
                        k: v
                        for k, v in {
                            "control_type": info.control_type or "",
                            "auto_id": info.automation_id or "",
                            "title": info.name or "",
                        }.items()
                        if v
                    }
                    self.events.put({"action": "click", "params": dict(target)})
                    last_target = target
                except Exception:  # noqa: BLE001 - a single bad resolution shouldn't kill the recording
                    continue

            elif item[0] == "key":
                key = item[1]
                name = getattr(key, "name", None)
                char = getattr(key, "char", None)
                if name in _COMMIT_KEY_NAMES:
                    flush_text()
                elif name == "backspace":
                    if pending_text:
                        pending_text.pop()
                elif name == "space":
                    pending_text.append(" ")
                elif char is not None:
                    pending_text.append(char)
                # other special keys (shift, ctrl, arrows, ...) are ignored for the MVP
