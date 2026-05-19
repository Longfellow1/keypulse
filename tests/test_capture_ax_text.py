from __future__ import annotations

import queue
import time

from keypulse.capture.watchers.ax_text import AXTextWatcher
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


class _FakeLoginWindow:
    def localizedName(self) -> str:
        return "loginwindow"

    def bundleIdentifier(self) -> str:
        return "com.apple.loginwindow"

    def processIdentifier(self) -> int:
        return 999


class _FakeWorkspaceLoginWindow:
    @staticmethod
    def sharedWorkspace():
        return _FakeWorkspaceLoginWindow()

    def frontmostApplication(self):
        return _FakeLoginWindow()


class _FakeAppKitLoginWindow:
    NSWorkspace = _FakeWorkspaceLoginWindow


def test_read_frontmost_ax_text_skips_loginwindow():
    payload = read_frontmost_ax_text(
        appkit_module=_FakeAppKitLoginWindow(),
        application_services_module=_FakeApplicationServices(),
    )

    assert payload == {
        "text": "",
        "selected_text": None,
        "value_text": None,
        "title_text": None,
        "app_name": None,
        "window_title": None,
        "process_name": None,
    }


def test_ax_text_watcher_throttles_ax_reads_on_tight_poll_interval():
    calls = 0

    def _reader():
        nonlocal calls
        calls += 1
        return {
            "text": "",
            "selected_text": None,
            "value_text": None,
            "title_text": None,
            "app_name": "Notes",
            "window_title": "window title",
            "process_name": "com.apple.Notes",
        }

    watcher = AXTextWatcher(
        queue.Queue(),
        poll_interval_sec=0.0,
        min_poll_interval_sec=1.0,
        max_reads_per_sec=1000.0,
        text_reader=_reader,
    )
    watcher.start()
    time.sleep(0.1)
    watcher.stop()

    # 100ms 内不应疯狂调用 AX API，至少受 1s 最小轮询间隔约束。
    assert calls <= 2
