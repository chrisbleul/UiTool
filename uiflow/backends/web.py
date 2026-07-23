from __future__ import annotations

from typing import Any

# Playwright wants modifier names capitalized ("Control", not "ctrl"); anything
# else (a single letter, or a named key like "F5"/"Enter") passes through as-is
# except single letters, which Playwright expects uppercased.
_WEB_MODIFIER_NAMES = {"ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift",
                        "win": "Meta", "cmd": "Meta", "meta": "Meta"}


def _translate_hotkey(keys: str) -> str:
    parts = [p.strip() for p in keys.split("+") if p.strip()]
    if not parts:
        raise ValueError("send_hotkey requires at least one key")
    translated = [
        _WEB_MODIFIER_NAMES.get(p.lower(), p.upper() if len(p) == 1 else p.capitalize()) for p in parts
    ]
    return "+".join(translated)


class WebBackend:
    """Browser automation backend built on Playwright.

    Playwright is imported lazily so `uiflow` can be used for desktop-only
    workflows without Playwright (and its browser binaries) installed.
    """

    def __init__(self, headless: bool = False, browser: str = "chromium"):
        self._headless = headless
        self._browser_name = browser
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, self._browser_name)
        self._browser = browser_type.launch(headless=self._headless)
        self._page = self._browser.new_page()

    @property
    def page(self) -> Any:
        if self._page is None:
            self.start()
        return self._page

    def navigate(self, url: str) -> None:
        self.page.goto(url)

    def click(self, selector: str, timeout: int = 10000) -> None:
        self.page.click(selector, timeout=timeout)

    def type(self, selector: str, text: str, timeout: int = 10000) -> None:
        self.page.fill(selector, text, timeout=timeout)

    def wait_for_selector(self, selector: str, timeout: int = 10000, state: str = "visible") -> None:
        self.page.wait_for_selector(selector, timeout=timeout, state=state)

    def get_text(self, selector: str, timeout: int = 10000) -> str:
        return self.page.inner_text(selector, timeout=timeout)

    def send_hotkey(self, keys: str) -> None:
        self.page.keyboard.press(_translate_hotkey(keys))

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(seconds * 1000)

    def screenshot(self, path: str) -> None:
        self.page.screenshot(path=path)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
