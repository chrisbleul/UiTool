import pytest

from uiflow.backends.desktop import _translate_hotkey as desktop_translate
from uiflow.backends.web import _translate_hotkey as web_translate


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("ctrl+s", "^s"),
        ("alt+f4", "%{F4}"),
        ("ctrl+shift+s", "^+s"),
        ("f5", "{F5}"),
        ("enter", "{ENTER}"),
    ],
)
def test_desktop_hotkey_translation(keys, expected):
    assert desktop_translate(keys) == expected


def test_desktop_hotkey_requires_at_least_one_key():
    with pytest.raises(ValueError):
        desktop_translate("")


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("ctrl+s", "Control+S"),
        ("alt+F4", "Alt+F4"),
        ("ctrl+shift+s", "Control+Shift+S"),
        ("F5", "F5"),
        ("Enter", "Enter"),
    ],
)
def test_web_hotkey_translation(keys, expected):
    assert web_translate(keys) == expected


def test_web_hotkey_requires_at_least_one_key():
    with pytest.raises(ValueError):
        web_translate("")
