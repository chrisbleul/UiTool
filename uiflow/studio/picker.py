"""'Click to detect' selector recognition, used by the Studio's "Element wählen"
button. Each function blocks the calling (request) thread until the user clicks
somewhere, then returns the detected selector - no job/queue bookkeeping needed
since Flask's dev server handles each request in its own thread."""

from __future__ import annotations

import time
from typing import Any

# Injected into the picked page: highlights the hovered element, and on click
# reports a best-effort CSS selector back to Python via window.__uiflow_report
# instead of letting the click actually activate the element (submit, navigate, ...).
_WEB_PICKER_JS = """
(function () {
  function cssSelector(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const path = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      let part = el.tagName.toLowerCase();
      const cls = (el.className && typeof el.className === 'string')
        ? el.className.trim().split(/\\s+/).filter(Boolean).slice(0, 2)
        : [];
      if (cls.length) part += '.' + cls.map((c) => CSS.escape(c)).join('.');
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
      }
      path.unshift(part);
      el = parent;
    }
    return path.join(' > ');
  }
  document.addEventListener('mouseover', function (e) {
    e.target.style.outline = '2px solid #dc2626';
  }, true);
  document.addEventListener('mouseout', function (e) {
    e.target.style.outline = '';
  }, true);
  document.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    window.__uiflow_report(cssSelector(e.target));
  }, true);
})();
"""


def pick_web_selector(url: str, timeout: float = 60.0) -> dict[str, Any]:
    """Opens `url` in a headed browser and waits for the user to click an element.
    Returns {"selector": "..."}. Raises TimeoutError if nothing was clicked in time."""
    from playwright.sync_api import sync_playwright

    result: dict[str, Any] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.expose_function("__uiflow_report", lambda selector: result.update(selector=selector))
            page.add_init_script(_WEB_PICKER_JS)
            page.goto(url)
            deadline = time.time() + timeout
            while "selector" not in result and time.time() < deadline:
                page.wait_for_timeout(200)
        finally:
            browser.close()

    if "selector" not in result:
        raise TimeoutError("Kein Element ausgewählt (Timeout)")
    return result


def _most_specific_element_at(x: int, y: int):
    """UI Automation's plain hit-test (from_point) often resolves to an outer
    container on WPF apps with owner-drawn/custom controls (observed on DoubleUI:
    a click on the "Cash In" textbox hit-tested to its whole parent panel instead).
    Walk the top-level window's descendants and pick the smallest element whose
    rectangle actually contains the point - much closer to what the user clicked."""
    from pywinauto import Desktop

    top = Desktop(backend="uia").from_point(x, y).top_level_parent()
    best = None
    best_area = None
    for descendant in top.descendants():
        try:
            r = descendant.rectangle()
        except Exception:  # noqa: BLE001 - some elements fail to report a rectangle
            continue
        if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
            continue
        area = (r.right - r.left) * (r.bottom - r.top)
        if area <= 0:
            continue
        if best_area is None or area < best_area:
            best, best_area = descendant, area
    return best if best is not None else Desktop(backend="uia").from_point(x, y)


class _HighlightOverlay:
    """A red rectangle drawn on screen around whatever element the mouse is
    currently over - like UiPath's/Inspect.exe's "indicate on screen" hover.

    Implemented as 4 thin borderless popup windows (one per edge) rather than a
    single window with custom painting: WS_EX_LAYERED + WS_EX_TRANSPARENT +
    WS_EX_TOOLWINDOW makes each one click-through, unfocusable, and not
    alt-tab-able, and a plain solid-colour class background needs no WM_PAINT
    handling. SWP_NOACTIVATE on every reposition is what keeps the highlighted
    application from losing focus to the overlay.
    """

    _CLASS_NAME = "UiflowHighlightBar"
    _THICKNESS = 3

    def __init__(self, colour: tuple[int, int, int] = (220, 38, 38)) -> None:
        import win32api
        import win32con
        import win32gui

        self._win32gui = win32gui
        self._win32con = win32con

        brush = win32gui.CreateSolidBrush(win32api.RGB(*colour))
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = win32gui.DefWindowProc
        wc.lpszClassName = self._CLASS_NAME
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.hbrBackground = brush
        try:
            win32gui.RegisterClass(wc)
        except Exception:  # noqa: BLE001 - already registered from a previous pick
            pass

        ex_style = (
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOPMOST
            | win32con.WS_EX_TOOLWINDOW
        )
        self._bars = []
        for _ in range(4):  # top, bottom, left, right
            hwnd = win32gui.CreateWindowEx(
                ex_style, self._CLASS_NAME, "", win32con.WS_POPUP,
                0, 0, 0, 0, 0, 0, win32api.GetModuleHandle(None), None,
            )
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)
            self._bars.append(hwnd)
        self._last_rect: tuple[int, int, int, int] | None = None

    def show(self, left: int, top: int, right: int, bottom: int) -> None:
        rect = (left, top, right, bottom)
        if rect == self._last_rect:
            return
        self._last_rect = rect
        t = self._THICKNESS
        flags = self._win32con.SWP_SHOWWINDOW | self._win32con.SWP_NOACTIVATE
        top_most = self._win32con.HWND_TOPMOST
        w, h = right - left, bottom - top
        self._win32gui.SetWindowPos(self._bars[0], top_most, left, top, w, t, flags)
        self._win32gui.SetWindowPos(self._bars[1], top_most, left, bottom - t, w, t, flags)
        self._win32gui.SetWindowPos(self._bars[2], top_most, left, top, t, h, flags)
        self._win32gui.SetWindowPos(self._bars[3], top_most, right - t, top, t, h, flags)

    def hide(self) -> None:
        self._last_rect = None
        for hwnd in self._bars:
            self._win32gui.ShowWindow(hwnd, self._win32con.SW_HIDE)

    def close(self) -> None:
        for hwnd in self._bars:
            try:
                self._win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        self._bars = []


def _focus_scope_window(title: str | None, path: str | None) -> None:
    """Brings the workflow's target application (from its `launch`/`connect` step)
    to the foreground before a pick starts, so the user doesn't have to Alt-Tab to
    it manually. Best-effort: if the app isn't running (yet) this just does nothing,
    and the plain `delay` still gives the user a manual fallback."""
    from pathlib import Path

    from pywinauto import Application

    app = Application(backend="uia").connect(title=title, timeout=3) if title else Application(
        backend="uia"
    ).connect(path=Path(path).name, timeout=3)
    app.top_window().set_focus()


def pick_desktop_element(
    timeout: float = 30.0,
    delay: float = 0.0,
    focus_title: str | None = None,
    focus_path: str | None = None,
) -> dict[str, Any]:
    """Blocks until the user clicks anywhere on the screen (or timeout), then
    returns UI Automation selector info for the element under that click.

    If `focus_title`/`focus_path` identify the workflow's scope application (its
    `connect`/`launch` step), that window is brought to the foreground first.
    `delay` additionally waits that many seconds before the click listener starts,
    for manual Alt-Tabbing when no scope is known yet.
    """
    import threading

    from pynput import mouse

    if focus_title or focus_path:
        try:
            _focus_scope_window(focus_title, focus_path)
        except Exception:  # noqa: BLE001 - best-effort; fall back to the plain delay below
            pass

    if delay > 0:
        time.sleep(delay)

    from pywinauto import Desktop as _Desktop

    result: dict[str, Any] = {}
    done = threading.Event()
    # Created lazily from inside on_move, i.e. on pynput's own hook thread - a
    # window's HWND has thread affinity, and constructing it on the thread that
    # *calls* pick_desktop_element (which is not the same thread pynput invokes
    # on_move/on_click on) made every SetWindowPos from on_move fail with
    # "invalid window handle" (Win32 error 1400), silently swallowed by the
    # try/except below and never actually visible.
    overlay_holder: list[_HighlightOverlay] = []
    last_move_t = [0.0]
    last_rect = [None]

    def on_move(x, y):
        now = time.time()
        if now - last_move_t[0] < 0.08:
            return
        last_move_t[0] = now
        try:
            if not overlay_holder:
                overlay_holder.append(_HighlightOverlay())
            r = _Desktop(backend="uia").from_point(x, y).rectangle()
            rect = (r.left, r.top, r.right, r.bottom)
            if rect != last_rect[0]:
                overlay_holder[0].show(*rect)
                last_rect[0] = rect
        except Exception:  # noqa: BLE001 - transient hit-test failures are expected while moving
            pass

    def on_click(x, y, button, pressed):
        if pressed:
            return True  # ignore mouse-down, act on release so the click has landed
        # Hiding the overlay here (not after listener.stop() below) matters for
        # the same thread-affinity reason as its lazy creation in on_move: this
        # runs on pynput's hook thread, same as the window's owning thread.
        if overlay_holder:
            overlay_holder[0].hide()
        result["x"], result["y"] = x, y
        done.set()
        return False  # stop the listener

    listener = mouse.Listener(on_move=on_move, on_click=on_click)
    listener.start()
    finished = done.wait(timeout=timeout)
    listener.stop()
    if overlay_holder:
        # Best-effort: the hook thread has already stopped by now, so this is a
        # cross-thread DestroyWindow (only reliably legal on the owning thread).
        # _HighlightOverlay.close() swallows failures per-window; worst case a
        # few invisible toolwindow popups are leaked until process exit.
        overlay_holder[0].close()

    if not finished:
        raise TimeoutError("Kein Klick erkannt (Timeout)")

    try:
        info = _most_specific_element_at(result["x"], result["y"]).element_info
        return {
            "control_type": info.control_type or "",
            "auto_id": info.automation_id or "",
            "title": info.name or "",
            "class_name": info.class_name or "",
        }
    except Exception as exc:  # noqa: BLE001 - surface resolution failure to the caller
        raise RuntimeError(str(exc)) from exc
