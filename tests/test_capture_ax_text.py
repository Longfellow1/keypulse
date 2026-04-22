from __future__ import annotations

from keypulse.capture.watchers.ax_text import read_frontmost_ax_text


class _FakeApp:
    def localizedName(self) -> str:
        return "Notes"

    def bundleIdentifier(self) -> str:
        return "com.apple.Notes"

    def processIdentifier(self) -> int:
        return 123


class _FakeWorkspace:
    @staticmethod
    def sharedWorkspace():
        return _FakeWorkspace()

    def frontmostApplication(self):
        return _FakeApp()


class _FakeAppKit:
    NSWorkspace = _FakeWorkspace


class _FakeApplicationServices:
    kAXErrorSuccess = 0

    @staticmethod
    def AXUIElementCreateApplication(pid: int):
        return f"app:{pid}"

    @staticmethod
    def AXUIElementCopyAttributeValue(element, attribute, _unused):
        mapping = {
            ("app:123", "AXFocusedUIElement"): ("focused", 0),
            ("app:123", "AXFocusedWindow"): ("window", 0),
            ("focused", "AXSelectedText"): ("picked text", 0),
            ("focused", "AXValue"): ("full value", 0),
            ("focused", "AXTitle"): ("field title", 0),
            ("window", "AXTitle"): ("window title", 0),
        }
        value, error = mapping.get((element, attribute), (None, 1))
        return error, value


def test_read_frontmost_ax_text_prefers_selected_text():
    payload = read_frontmost_ax_text(
        appkit_module=_FakeAppKit(),
        application_services_module=_FakeApplicationServices(),
    )

    assert payload == {
        "text": "picked text",
        "selected_text": "picked text",
        "value_text": "full value",
        "title_text": "field title",
        "app_name": "Notes",
        "window_title": "window title",
        "process_name": "com.apple.Notes",
    }


class _FakeApplicationServicesMissingText(_FakeApplicationServices):
    @staticmethod
    def AXUIElementCopyAttributeValue(element, attribute, _unused):
        mapping = {
            ("app:123", "AXFocusedUIElement"): ("focused", 0),
            ("app:123", "AXFocusedWindow"): ("window", 0),
            ("focused", "AXSelectedText"): (None, 1),
            ("focused", "AXValue"): (None, 1),
            ("focused", "AXTitle"): (None, 1),
            ("window", "AXTitle"): ("window title", 0),
        }
        value, error = mapping.get((element, attribute), (None, 1))
        return error, value


def test_read_frontmost_ax_text_returns_empty_text_safely_when_unavailable():
    payload = read_frontmost_ax_text(
        appkit_module=_FakeAppKit(),
        application_services_module=_FakeApplicationServicesMissingText(),
    )

    assert payload == {
        "text": "",
        "selected_text": None,
        "value_text": None,
        "title_text": None,
        "app_name": "Notes",
        "window_title": "window title",
        "process_name": "com.apple.Notes",
    }
