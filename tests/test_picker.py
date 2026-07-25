"""Tests for picker.inspect_web_selector - the read-only "how many elements
does this selector match" check. Fakes the small slice of Playwright's API it
actually calls (chromium.launch -> new_page -> goto/locator/nth/evaluate/
inner_text/is_visible) rather than launching a real browser, matching how the
rest of this project keeps external services (Playwright, pywinauto, SMTP,
IMAP, ...) mocked out of the test suite."""

import pytest

from uiflow.studio import picker


class _FakeElement:
    def __init__(self, tag, text, visible=True, evaluate_error=None):
        self._tag = tag
        self._text = text
        self._visible = visible
        self._evaluate_error = evaluate_error

    def evaluate(self, _script):
        if self._evaluate_error:
            raise self._evaluate_error
        return self._tag

    def inner_text(self, timeout=None):
        return self._text

    def is_visible(self):
        return self._visible


class _FakeLocator:
    def __init__(self, elements):
        self._elements = elements

    def count(self):
        return len(self._elements)

    def nth(self, index):
        return self._elements[index]


class _FakeInvalidLocator:
    def count(self):
        raise Exception("invalid selector syntax")  # noqa: TRY002 - mimics Playwright's own broad raise


class _FakePage:
    def __init__(self, elements_by_selector, goto_error=None):
        self._elements_by_selector = elements_by_selector
        self._goto_error = goto_error
        self.goto_calls = []

    def goto(self, url, timeout=None):
        self.goto_calls.append((url, timeout))
        if self._goto_error:
            raise self._goto_error

    def locator(self, selector):
        if selector == "$invalid":
            return _FakeInvalidLocator()
        return _FakeLocator(self._elements_by_selector.get(selector, []))


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser
        self.launch_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self._browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class _FakeSyncPlaywrightContext:
    def __init__(self, chromium):
        self._chromium = chromium

    def __enter__(self):
        return _FakePlaywright(self._chromium)

    def __exit__(self, exc_type, exc, tb):
        return False


def _install_fake_playwright(monkeypatch, elements_by_selector, goto_error=None):
    page = _FakePage(elements_by_selector, goto_error=goto_error)
    browser = _FakeBrowser(page)
    chromium = _FakeChromium(browser)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeSyncPlaywrightContext(chromium))
    return browser, page, chromium


def test_inspect_web_selector_reports_count_and_per_match_info(monkeypatch):
    elements = {
        "button": [
            _FakeElement("button", "Absenden"),
            _FakeElement("button", "Abbrechen", visible=False),
        ]
    }
    browser, page, _chromium = _install_fake_playwright(monkeypatch, elements)

    result = picker.inspect_web_selector("https://example.com", "button")

    assert result == {
        "count": 2,
        "matches": [
            {"tag": "button", "text": "Absenden", "visible": True},
            {"tag": "button", "text": "Abbrechen", "visible": False},
        ],
    }
    assert page.goto_calls == [("https://example.com", 15000)]
    assert browser.closed is True  # cleaned up even on the happy path


def test_inspect_web_selector_reports_zero_for_no_match(monkeypatch):
    _install_fake_playwright(monkeypatch, {})

    result = picker.inspect_web_selector("https://example.com", "#gibtsnicht")

    assert result == {"count": 0, "matches": []}


def test_inspect_web_selector_caps_matches_but_not_the_reported_count(monkeypatch):
    elements = {"div": [_FakeElement("div", f"item {i}") for i in range(30)]}
    _install_fake_playwright(monkeypatch, elements)

    result = picker.inspect_web_selector("https://example.com", "div")

    assert result["count"] == 30
    assert len(result["matches"]) == picker._MAX_INSPECT_MATCHES


def test_inspect_web_selector_raises_value_error_for_invalid_selector(monkeypatch):
    _install_fake_playwright(monkeypatch, {})

    with pytest.raises(ValueError):
        picker.inspect_web_selector("https://example.com", "$invalid")


def test_inspect_web_selector_raises_value_error_when_the_page_fails_to_load(monkeypatch):
    browser, _page, _chromium = _install_fake_playwright(
        monkeypatch, {}, goto_error=TimeoutError("navigation timeout")
    )

    with pytest.raises(ValueError):
        picker.inspect_web_selector("https://unreachable.example", "button")

    assert browser.closed is True  # still cleaned up when goto() fails


def test_inspect_web_selector_launches_headless(monkeypatch):
    elements = {"button": [_FakeElement("button", "x")]}
    _browser, _page, chromium = _install_fake_playwright(monkeypatch, elements)

    picker.inspect_web_selector("https://example.com", "button")

    assert chromium.launch_kwargs == {"headless": True}


# --- inspect_desktop_selector -------------------------------------------------
#
# pywinauto isn't even installed in this sandbox (it's Windows-only), so a
# fake module is inserted into sys.modules rather than monkeypatching an
# attribute on a real one - same "fake the external API surface" approach as
# above, just one level lower since there's no real module to patch onto.


class _FakeDesktopElementInfo:
    def __init__(self, control_type="", name="", automation_id=""):
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id


class _FakeDesktopElement:
    def __init__(self, control_type="", name="", automation_id=""):
        self.element_info = _FakeDesktopElementInfo(control_type, name, automation_id)


class _FakeDesktopWindow:
    def __init__(self, elements, descendants_error=None):
        self._elements = elements
        self._descendants_error = descendants_error

    def descendants(self, **criteria):
        if self._descendants_error:
            raise self._descendants_error

        def matches(el):
            info = el.element_info
            if "control_type" in criteria and info.control_type != criteria["control_type"]:
                return False
            if "title" in criteria and info.name != criteria["title"]:
                return False
            if "auto_id" in criteria and info.automation_id != criteria["auto_id"]:
                return False
            return True

        return [el for el in self._elements if matches(el)]


def _install_fake_pywinauto(monkeypatch, window=None, connect_error=None):
    import sys
    import types

    class _FakeApplication:
        def __init__(self, backend="uia"):
            self._window = window

        def connect(self, **kwargs):
            if connect_error:
                raise connect_error
            return self

        def top_window(self):
            return self._window

    fake_module = types.ModuleType("pywinauto")
    fake_module.Application = _FakeApplication
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)


def test_inspect_desktop_selector_reports_count_and_per_match_info(monkeypatch):
    window = _FakeDesktopWindow(
        [
            _FakeDesktopElement(control_type="Button", name="OK", automation_id="btnOK"),
            _FakeDesktopElement(control_type="Button", name="Abbrechen", automation_id="btnCancel"),
        ]
    )
    _install_fake_pywinauto(monkeypatch, window=window)

    result = picker.inspect_desktop_selector(focus_title="Editor", control_type="Button")

    assert result == {
        "count": 2,
        "matches": [
            {"control_type": "Button", "title": "OK", "auto_id": "btnOK"},
            {"control_type": "Button", "title": "Abbrechen", "auto_id": "btnCancel"},
        ],
    }


def test_inspect_desktop_selector_reports_zero_for_no_match(monkeypatch):
    window = _FakeDesktopWindow([_FakeDesktopElement(control_type="Edit", name="Suche")])
    _install_fake_pywinauto(monkeypatch, window=window)

    result = picker.inspect_desktop_selector(focus_title="Editor", control_type="Button")

    assert result == {"count": 0, "matches": []}


def test_inspect_desktop_selector_caps_matches_but_not_the_reported_count(monkeypatch):
    window = _FakeDesktopWindow([_FakeDesktopElement(control_type="ListItem", name=f"Zeile {i}") for i in range(30)])
    _install_fake_pywinauto(monkeypatch, window=window)

    result = picker.inspect_desktop_selector(focus_title="Editor", control_type="ListItem")

    assert result["count"] == 30
    assert len(result["matches"]) == picker._MAX_INSPECT_MATCHES


def test_inspect_desktop_selector_requires_a_focus_title_or_path(monkeypatch):
    _install_fake_pywinauto(monkeypatch, window=_FakeDesktopWindow([]))

    with pytest.raises(ValueError):
        picker.inspect_desktop_selector(control_type="Button")


def test_inspect_desktop_selector_raises_value_error_when_the_app_is_unreachable(monkeypatch):
    _install_fake_pywinauto(monkeypatch, connect_error=TimeoutError("not found"))

    with pytest.raises(ValueError):
        picker.inspect_desktop_selector(focus_title="Editor", control_type="Button")


def test_inspect_desktop_selector_raises_value_error_for_invalid_selector(monkeypatch):
    window = _FakeDesktopWindow([], descendants_error=Exception("bad criteria"))
    _install_fake_pywinauto(monkeypatch, window=window)

    with pytest.raises(ValueError):
        picker.inspect_desktop_selector(focus_title="Editor", not_a_real_kwarg="x")
