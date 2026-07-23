from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# pywinauto's keyboard.send_keys() uses its own mini-syntax: ^ = ctrl, % = alt,
# + = shift, and multi-character key names go in braces (e.g. "{F5}", "{ENTER}").
# send_hotkey() takes the more familiar "ctrl+s" / "alt+f4" form and translates.
_HOTKEY_MODIFIER_PREFIXES = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}


def _translate_hotkey(keys: str) -> str:
    parts = [p.strip() for p in keys.split("+") if p.strip()]
    if not parts:
        raise ValueError("send_hotkey requires at least one key")
    *modifiers, key = parts
    prefix = "".join(_HOTKEY_MODIFIER_PREFIXES.get(m.lower(), "") for m in modifiers)
    key_token = key if len(key) == 1 else "{" + key.upper() + "}"
    return prefix + key_token


class DesktopBackend:
    """Windows desktop automation backend built on pywinauto (UI Automation).

    pywinauto is imported lazily so `uiflow` can be used for web-only
    workflows on non-Windows machines without pywinauto installed.
    """

    def __init__(self, automation: str = "uia"):
        self._automation = automation
        self._app: Any = None
        self._window: Any = None

    def launch(self, path: str, timeout: int = 15) -> None:
        from pywinauto import Application

        # A bare relative filename with no path separator (e.g. "DoubleUI.exe", as
        # opposed to ".\DoubleUI.exe") is *not* resolved against the current working
        # directory by Windows' CreateProcess (SafeProcessSearchMode skips CWD search
        # unless the string already contains a path element) - only against the
        # calling process's own directory, System32/Windows, and PATH. That silently
        # broke launching a relative-path exe living in the project directory
        # (worked for "notepad.exe" only because System32 is always searched).
        # Resolving to an absolute path up front sidesteps the whole distinction.
        resolved_path = str(Path(path).resolve()) if Path(path).exists() else path

        # On Windows 11, some System32 executables (e.g. notepad.exe) are stubs that
        # relaunch as a UWP-packaged process under a different PID, so the PID
        # Application.start() returns often no longer owns any window. Re-resolve the
        # app by executable name instead of trusting that PID. Any pywinauto error
        # during this window (not-found, ambiguous match against a leftover instance,
        # timeout) is treated as "not ready yet" and retried until the deadline.
        Application(backend=self._automation).start(resolved_path)
        exe_name = Path(path).name
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                self._app = Application(backend=self._automation).connect(path=exe_name, timeout=1)
                self._window = self._app.top_window()
                self._window.wait("exists enabled visible ready", timeout=timeout)
                return
            except Exception as exc:  # noqa: BLE001 - broad on purpose, see comment above
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"Could not find a window for '{path}' after launch") from last_error

    def connect(
        self,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        timeout: int = 10,
    ) -> None:
        from pywinauto import Application

        # pywinauto treats an explicitly-passed None as a real criterion (e.g. searches
        # for process=None) instead of "unset", so only forward the ones actually given.
        criteria = {
            k: v
            for k, v in {"title": title, "title_re": title_re, "process": process}.items()
            if v is not None
        }
        if not criteria:
            raise ValueError("connect() requires at least one of: title, title_re, process")
        self._app = Application(backend=self._automation).connect(timeout=timeout, **criteria)
        self._window = self._app.top_window()

    def _resolve(self, timeout: int = 10, **selector: Any) -> Any:
        if self._window is None:
            raise RuntimeError("No window: call 'launch' or 'connect' first")
        # Bring the scope application to the foreground before every interaction -
        # mirrors UiPath's "Use Application/Browser" scope activity, and protects
        # against another window having stolen focus between steps.
        try:
            self._window.set_focus()
        except Exception:  # noqa: BLE001 - best-effort; resolution below still proceeds
            pass
        control = self._window.child_window(**selector)
        control.wait("exists enabled visible ready", timeout=timeout)
        return control

    def wait_for_element(self, timeout: int = 10, state: str = "exists", **selector: Any) -> None:
        if state == "gone":
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if not self._window.child_window(**selector).exists():
                        return
                except Exception:  # noqa: BLE001 - treat any resolution failure as "gone"
                    return
                time.sleep(0.2)
            raise TimeoutError(f"Element still present after {timeout}s: {selector}")
        self._resolve(timeout=timeout, **selector)

    def click(self, timeout: int = 10, double: bool = False, **selector: Any) -> None:
        control = self._resolve(timeout=timeout, **selector)
        if double:
            control.double_click_input()
        else:
            control.click_input()

    def type(self, text: str, timeout: int = 10, **selector: Any) -> None:
        control = self._resolve(timeout=timeout, **selector)
        control.set_focus()
        control.type_keys(text, with_spaces=True)

    def get_text(self, timeout: int = 10, **selector: Any) -> str:
        control = self._resolve(timeout=timeout, **selector)
        try:
            return control.iface_value.CurrentValue
        except Exception:  # noqa: BLE001 - not every control supports ValuePattern
            return control.window_text()

    def send_hotkey(self, keys: str) -> None:
        from pywinauto.keyboard import send_keys

        if self._window is not None:
            try:
                self._window.set_focus()
            except Exception:  # noqa: BLE001 - best-effort; send_keys below still proceeds
                pass
        send_keys(_translate_hotkey(keys))

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def screenshot(self, path: str) -> None:
        if self._window is None:
            raise RuntimeError("No window to screenshot")
        image = self._window.capture_as_image()
        image.save(path)

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
