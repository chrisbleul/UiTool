"""Tests for the recorder's event-to-step interpretation (_RecordingSession),
which is deliberately free of threads/queues/OS hooks so it can be exercised
here with fake UI elements - no pywinauto/pynput/Windows required. Recorder
itself (the pynput/pywinauto plumbing around it) is not covered: it needs a
real desktop to observe anything meaningful and isn't unit-testable in this
sandbox."""

from uiflow.studio import recorder as recorder_module
from uiflow.studio.recorder import _RecordingSession


class _FakeElementInfo:
    def __init__(self, control_type="", automation_id="", name=""):
        self.control_type = control_type
        self.automation_id = automation_id
        self.name = name


class _FakeTopLevel:
    def __init__(self, pid, handle=None):
        self._pid = pid
        self.handle = handle

    def process_id(self):
        return self._pid


class _FakeElement:
    def __init__(self, control_type="Button", name="", pid=100, handle=None, auto_id=""):
        self.element_info = _FakeElementInfo(control_type=control_type, automation_id=auto_id, name=name)
        self._top = _FakeTopLevel(pid, handle)

    def top_level_parent(self):
        return self._top


def _patch_resolver(monkeypatch, mapping):
    """Feeds `_RecordingSession`'s calls to `_most_specific_element_at` from a
    {(x, y): _FakeElement} mapping instead of touching real UI Automation."""
    monkeypatch.setattr(recorder_module, "_most_specific_element_at", lambda x, y: mapping[(x, y)])


def test_click_in_scope_emits_click_step(monkeypatch):
    _patch_resolver(monkeypatch, {(10, 10): _FakeElement(control_type="Button", name="OK", pid=100)})
    session = _RecordingSession(scope_pid=100)

    events = session.handle_mouse(10, 10, "left", True)
    assert events == []
    events = session.handle_mouse(10, 10, "left", False)

    assert events == [{"action": "click", "params": {"control_type": "Button", "title": "OK"}}]


def test_click_outside_scope_is_ignored(monkeypatch):
    _patch_resolver(monkeypatch, {(10, 10): _FakeElement(control_type="Button", name="OK", pid=999)})
    session = _RecordingSession(scope_pid=100)

    session.handle_mouse(10, 10, "left", True)
    events = session.handle_mouse(10, 10, "left", False)

    assert events == []


def test_right_click_is_captured_with_button_param(monkeypatch):
    _patch_resolver(monkeypatch, {(10, 10): _FakeElement(control_type="ListItem", name="Zeile 1", pid=100)})
    session = _RecordingSession(scope_pid=100)

    session.handle_mouse(10, 10, "right", True)
    events = session.handle_mouse(10, 10, "right", False)

    assert events == [
        {"action": "click", "params": {"control_type": "ListItem", "title": "Zeile 1", "button": "right"}}
    ]


def test_small_mouse_movement_is_still_a_click_not_a_drag(monkeypatch):
    _patch_resolver(monkeypatch, {(10, 10): _FakeElement(control_type="Button", name="OK", pid=100)})
    session = _RecordingSession(scope_pid=100)

    session.handle_mouse(10, 10, "left", True)
    events = session.handle_mouse(12, 11, "left", False)  # 2px jitter, below the drag threshold

    assert events == [{"action": "click", "params": {"control_type": "Button", "title": "OK"}}]


def test_large_mouse_movement_emits_drag_step_with_destination_coords(monkeypatch):
    _patch_resolver(monkeypatch, {(10, 10): _FakeElement(control_type="ListItem", name="Zeile 3", pid=100)})
    session = _RecordingSession(scope_pid=100)

    session.handle_mouse(10, 10, "left", True)
    events = session.handle_mouse(200, 300, "left", False)

    assert events == [
        {
            "action": "drag",
            "params": {"control_type": "ListItem", "title": "Zeile 3", "to_x": 200, "to_y": 300},
        }
    ]


def test_scroll_events_on_the_same_target_coalesce_into_one_step(monkeypatch):
    _patch_resolver(monkeypatch, {(50, 50): _FakeElement(control_type="List", name="Ergebnisse", pid=100)})
    session = _RecordingSession(scope_pid=100)

    assert session.handle_scroll(50, 50, -1) == []
    assert session.handle_scroll(50, 50, -1) == []
    events = session.flush()

    assert events == [
        {"action": "scroll", "params": {"control_type": "List", "title": "Ergebnisse", "amount": -2}}
    ]


def test_scroll_on_a_new_target_flushes_the_previous_one(monkeypatch):
    _patch_resolver(
        monkeypatch,
        {
            (10, 10): _FakeElement(control_type="List", name="A", pid=100),
            (20, 20): _FakeElement(control_type="List", name="B", pid=100),
        },
    )
    session = _RecordingSession(scope_pid=100)

    session.handle_scroll(10, 10, -1)
    events = session.handle_scroll(20, 20, -1)

    assert events == [{"action": "scroll", "params": {"control_type": "List", "title": "A", "amount": -1}}]


def test_typed_text_is_coalesced_and_flushed_on_enter(monkeypatch):
    _patch_resolver(monkeypatch, {(5, 5): _FakeElement(control_type="Edit", name="Suche", pid=100)})
    session = _RecordingSession(scope_pid=100)

    session.handle_mouse(5, 5, "left", True)
    session.handle_mouse(5, 5, "left", False)
    for ch in "hallo":
        assert session.handle_key_press(None, ch) == []
    events = session.handle_key_press("enter", None)

    assert events == [{"action": "type", "params": {"control_type": "Edit", "title": "Suche", "text": "hallo"}}]


def test_shift_held_still_produces_literal_text_not_a_hotkey(monkeypatch):
    # pynput resolves key.char through the held Shift state itself (Shift+a -> 'A'),
    # so bare Shift must never be treated as a hotkey trigger - regression guard.
    _patch_resolver(monkeypatch, {(5, 5): _FakeElement(control_type="Edit", name="Suche", pid=100)})
    session = _RecordingSession(scope_pid=100)
    session.handle_mouse(5, 5, "left", True)
    session.handle_mouse(5, 5, "left", False)

    assert session.handle_key_press("shift", None) == []
    assert session.handle_key_press(None, "A") == []
    session.handle_key_release("shift")
    events = session.handle_key_press("enter", None)

    assert events == [{"action": "type", "params": {"control_type": "Edit", "title": "Suche", "text": "A"}}]


def test_ctrl_plus_key_emits_send_hotkey_and_does_not_leak_into_text(monkeypatch):
    _patch_resolver(monkeypatch, {(5, 5): _FakeElement(control_type="Edit", name="Doc", pid=100)})
    session = _RecordingSession(scope_pid=100)
    session.handle_mouse(5, 5, "left", True)
    session.handle_mouse(5, 5, "left", False)

    assert session.handle_key_press("ctrl_l", None) == []
    events = session.handle_key_press(None, "s")

    assert events == [{"action": "send_hotkey", "params": {"keys": "ctrl+s"}}]
    assert session.pending_text == []


def test_ctrl_shift_plus_key_orders_modifiers_consistently(monkeypatch):
    session = _RecordingSession(scope_pid=100)

    session.handle_key_press("ctrl_l", None)
    session.handle_key_press("shift", None)
    events = session.handle_key_press(None, "s")

    assert events == [{"action": "send_hotkey", "params": {"keys": "ctrl+shift+s"}}]


def test_modifier_release_clears_hotkey_mode(monkeypatch):
    _patch_resolver(monkeypatch, {(5, 5): _FakeElement(control_type="Edit", name="Doc", pid=100)})
    session = _RecordingSession(scope_pid=100)
    session.handle_mouse(5, 5, "left", True)
    session.handle_mouse(5, 5, "left", False)

    session.handle_key_press("ctrl_l", None)
    session.handle_key_press(None, "s")  # ctrl+s hotkey
    session.handle_key_release("ctrl_l")
    events = session.handle_key_press(None, "x")  # plain typing again

    assert events == []
    assert session.pending_text == ["x"]


def test_owner_chain_widens_scope_to_a_second_window(monkeypatch):
    # A dialog hosted under a different pid but owned (per Windows' own GW_OWNER
    # relationship) by a window whose process is already in scope.
    _patch_resolver(monkeypatch, {(1, 1): _FakeElement(control_type="Dialog", name="Speichern unter", pid=555, handle=42)})

    def owner_pid_of(hwnd):
        if hwnd == 42:
            return 100, 7  # owned by hwnd 7, which belongs to pid 100 (the scope)
        return None, None

    session = _RecordingSession(scope_pid=100, owner_pid_of=owner_pid_of)

    session.handle_mouse(1, 1, "left", True)
    events = session.handle_mouse(1, 1, "left", False)

    assert events == [{"action": "click", "params": {"control_type": "Dialog", "title": "Speichern unter"}}]
    assert 555 in session._scope_pids  # remembered for subsequent clicks in the same dialog


def test_unrelated_window_stays_out_of_scope_even_with_owner_lookup(monkeypatch):
    _patch_resolver(monkeypatch, {(1, 1): _FakeElement(control_type="Window", name="Andere App", pid=999, handle=1)})
    session = _RecordingSession(scope_pid=100, owner_pid_of=lambda hwnd: (None, None))

    session.handle_mouse(1, 1, "left", True)
    events = session.handle_mouse(1, 1, "left", False)

    assert events == []


def test_stop_equivalent_flush_commits_pending_text_and_scroll(monkeypatch):
    _patch_resolver(
        monkeypatch,
        {
            (5, 5): _FakeElement(control_type="Edit", name="Feld", pid=100),
            (9, 9): _FakeElement(control_type="List", name="Liste", pid=100),
        },
    )
    session = _RecordingSession(scope_pid=100)
    session.handle_mouse(5, 5, "left", True)
    session.handle_mouse(5, 5, "left", False)
    session.handle_key_press(None, "x")

    events = session.flush()

    assert events == [{"action": "type", "params": {"control_type": "Edit", "title": "Feld", "text": "x"}}]
