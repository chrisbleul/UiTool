"""Recording mode: watches real clicks/typing against the scope application and
turns them into workflow steps live.

Threading follows the same rules learned the hard way in picker.py: pynput's
mouse/keyboard hook callbacks must stay trivial and return fast (Windows
silently disables a low-level hook that doesn't), so they only push raw events
onto a queue. All the actual (slower, UI-Automation-heavy) resolution happens
in a separate worker thread that drains that queue - never inside a hook
callback, and never touching UI Automation concurrently *with* a hook thread
doing the same (that combination reliably broke click detection during
development).

The event-to-step *interpretation* (what a click/drag/scroll/keystroke means
in terms of workflow steps) lives in _RecordingSession, a plain object with no
threads, queues or OS hooks of its own - Recorder below is the thin plumbing
that feeds it real pynput/pywinauto events. That split is what makes the
interpretation logic unit-testable without pywinauto/pynput installed (see
tests/test_recorder.py): tests drive _RecordingSession directly with fake
elements and synthetic events."""

from __future__ import annotations

import math
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .picker import _most_specific_element_at

_COMMIT_KEY_NAMES = {"enter", "tab"}

# pynput key names -> the modifier they represent. Left/right variants collapse
# to the same token since a hotkey doesn't care which physical key was held.
_MODIFIER_KEY_NAMES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "ctrl": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt", "alt": "alt",
    "shift": "shift", "shift_r": "shift",
}
# Only these turn the *next* keypress into a hotkey rather than literal text.
# Shift alone must NOT trigger that: pynput already resolves key.char through
# the held Shift state (Shift+a -> 'A', Shift+1 -> '!'), so treating bare Shift
# as a hotkey trigger would break capital letters and shifted symbols during
# ordinary typing. Shift is still included in the *combo string* below once
# ctrl/alt has already triggered hotkey mode (e.g. ctrl+shift+s).
_HOTKEY_TRIGGER_MODIFIERS = {"ctrl", "alt"}
_MODIFIER_COMBO_ORDER = ["ctrl", "alt", "shift"]

# How far (in screen pixels) a mouse-down -> mouse-up pair has to move before
# it counts as a drag instead of a click.
_DRAG_THRESHOLD_PX = 8

OwnerLookup = Callable[[int], "tuple[Optional[int], Optional[int]]"]


def _selector_fields(el: Any) -> dict[str, str]:
    info = el.element_info
    return {
        k: v
        for k, v in {
            "control_type": info.control_type or "",
            "auto_id": info.automation_id or "",
            "title": info.name or "",
        }.items()
        if v
    }


def _default_owner_pid_of(hwnd: int) -> "tuple[Optional[int], Optional[int]]":
    """Windows' own "this popup belongs to that window" relationship (used for
    modal dialogs, common-dialog broker processes, etc.), independent of
    process id - the same mismatch that forced `DesktopBackend.launch()` to
    re-resolve by executable name instead of trusting a captured pid (some
    apps hand off to a differently-pid'd process for their real window).
    Returns (owner_pid, owner_hwnd), both None if `hwnd` has no owner."""
    import win32con
    import win32gui
    import win32process

    owner_hwnd = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
    if not owner_hwnd:
        return None, None
    _, pid = win32process.GetWindowThreadProcessId(owner_hwnd)
    return pid, owner_hwnd


class _RecordingSession:
    """Turns raw mouse/keyboard events into workflow-step dicts. No threads,
    queues or OS hooks - just state (pending text, pending scroll, held
    modifiers, in-scope processes) and pure decisions, so it can be driven
    directly from tests with fake elements."""

    def __init__(self, scope_pid: int, owner_pid_of: Optional[OwnerLookup] = None) -> None:
        self._scope_pids = {scope_pid}
        self._owner_pid_of = owner_pid_of
        self.pending_text: list[str] = []
        self.last_target: Optional[dict[str, str]] = None
        self.held_modifiers: set[str] = set()
        self._press: Optional[tuple[int, int, str]] = None
        self._pending_scroll: Optional[dict[str, Any]] = None

    # --- scope -----------------------------------------------------------

    def _in_scope(self, top_level: Any) -> bool:
        pid = top_level.process_id()
        if pid in self._scope_pids:
            return True
        if self._owner_pid_of is None:
            return False
        # Walk the owner chain: a window (dialog, popup, ...) owned - directly
        # or transitively - by a window whose process is already in scope
        # counts as in scope too, even hosted under a different pid.
        hwnd = getattr(top_level, "handle", None)
        seen: set[int] = set()
        while hwnd and hwnd not in seen:
            seen.add(hwnd)
            owner_pid, owner_hwnd = self._owner_pid_of(hwnd)
            if owner_pid is None:
                return False
            if owner_pid in self._scope_pids:
                self._scope_pids.add(pid)  # remember it for next time too
                return True
            hwnd = owner_hwnd
        return False

    # --- flushing ----------------------------------------------------------

    def flush(self) -> list[dict]:
        """Commits whatever is pending (typed text, an in-progress scroll) as
        step(s) - called before a new, different interaction starts and when
        recording stops, mirroring how a real UiPath-style recorder treats a
        run of keystrokes/wheel-notches on the same element as one step."""
        events: list[dict] = []
        if self.pending_text and self.last_target:
            events.append({"action": "type", "params": {**self.last_target, "text": "".join(self.pending_text)}})
        self.pending_text = []
        if self._pending_scroll:
            events.append(
                {
                    "action": "scroll",
                    "params": {**self._pending_scroll["target"], "amount": self._pending_scroll["amount"]},
                }
            )
            self._pending_scroll = None
        return events

    # --- mouse ---------------------------------------------------------------

    def handle_mouse(self, x: int, y: int, button: str, pressed: bool) -> list[dict]:
        if pressed:
            self._press = (x, y, button)
            return []
        if self._press is None:
            return []  # a release without a matching press (e.g. recording started mid-drag)
        px, py, press_button = self._press
        self._press = None
        try:
            el = _most_specific_element_at(px, py)
            if not self._in_scope(el.top_level_parent()):
                return []
        except Exception:  # noqa: BLE001 - a single bad resolution shouldn't kill the recording
            return []

        events = self.flush()
        target = _selector_fields(el)
        if math.hypot(x - px, y - py) >= _DRAG_THRESHOLD_PX:
            params: dict[str, Any] = {**target, "to_x": x, "to_y": y}
            if press_button != "left":
                params["button"] = press_button
            events.append({"action": "drag", "params": params})
            self.last_target = None
        else:
            params = dict(target)
            if press_button != "left":
                params["button"] = press_button
            events.append({"action": "click", "params": params})
            self.last_target = target
        return events

    def handle_scroll(self, x: int, y: int, dy: int) -> list[dict]:
        try:
            el = _most_specific_element_at(x, y)
            if not self._in_scope(el.top_level_parent()):
                return []
        except Exception:  # noqa: BLE001
            return []

        target = _selector_fields(el)
        if self._pending_scroll is not None and self._pending_scroll["target"] == target:
            self._pending_scroll["amount"] += dy
            return []
        # A different (or first) scroll target commits whatever was pending -
        # a run of wheel notches on the same element becomes one `scroll` step
        # instead of flooding the workflow with one per notch.
        events = self.flush()
        self._pending_scroll = {"target": target, "amount": dy}
        self.last_target = None
        return events

    # --- keyboard ------------------------------------------------------------

    def handle_key_press(self, name: Optional[str], char: Optional[str]) -> list[dict]:
        modifier = _MODIFIER_KEY_NAMES.get(name or "")
        if modifier:
            self.held_modifiers.add(modifier)
            return []

        if self.held_modifiers & _HOTKEY_TRIGGER_MODIFIERS:
            key_token = (char or name or "").lower()
            if not key_token:
                return []
            events = self.flush()
            combo = "+".join([m for m in _MODIFIER_COMBO_ORDER if m in self.held_modifiers] + [key_token])
            events.append({"action": "send_hotkey", "params": {"keys": combo}})
            self.last_target = None
            return events

        if name in _COMMIT_KEY_NAMES:
            return self.flush()
        if name == "backspace":
            if self.pending_text:
                self.pending_text.pop()
            return []
        if name == "space":
            self.pending_text.append(" ")
            return []
        if char is not None:
            self.pending_text.append(char)
            return []
        return []  # other special keys (arrows, bare function keys, ...) - ignored for the MVP

    def handle_key_release(self, name: Optional[str]) -> None:
        modifier = _MODIFIER_KEY_NAMES.get(name or "")
        if modifier:
            self.held_modifiers.discard(modifier)


class Recorder:
    def __init__(self) -> None:
        self.events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._raw: "queue.Queue[tuple]" = queue.Queue()
        self._stop_event = threading.Event()
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._worker: threading.Thread | None = None
        self._session: Optional[_RecordingSession] = None

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
        self._session = _RecordingSession(window.process_id(), owner_pid_of=_default_owner_pid_of)

        def on_click(x, y, button, pressed):
            self._raw.put(("mouse", x, y, getattr(button, "name", str(button)), pressed))
            return not self._stop_event.is_set()

        def on_scroll(x, y, dx, dy):
            self._raw.put(("scroll", x, y, dy))
            return not self._stop_event.is_set()

        def on_press(key):
            self._raw.put(("press", getattr(key, "name", None), getattr(key, "char", None)))
            return not self._stop_event.is_set()

        def on_release(key):
            self._raw.put(("release", getattr(key, "name", None)))
            return not self._stop_event.is_set()

        self._mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        self._keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
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
        assert self._session is not None
        session = self._session
        while True:
            item = self._raw.get()
            if item[0] == "__stop__":
                for event in session.flush():
                    self.events.put(event)
                return
            if item[0] == "mouse":
                _, x, y, button, pressed = item
                for event in session.handle_mouse(x, y, button, pressed):
                    self.events.put(event)
            elif item[0] == "scroll":
                _, x, y, dy = item
                for event in session.handle_scroll(x, y, dy):
                    self.events.put(event)
            elif item[0] == "press":
                _, name, char = item
                for event in session.handle_key_press(name, char):
                    self.events.put(event)
            elif item[0] == "release":
                _, name = item
                session.handle_key_release(name)
