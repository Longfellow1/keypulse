from __future__ import annotations

import json
import queue
from unittest.mock import patch

from keypulse.capture.watchers.window import WindowWatcher


def _make_watcher(browser_app_names=None) -> WindowWatcher:
    return WindowWatcher(queue.Queue(), browser_app_names=browser_app_names)


def test_window_watcher_emits_title_change_event_for_same_app():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Code", "main.py", "com.microsoft.VSCode"),
                ("Code", "other.py", "com.microsoft.VSCode"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 1.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is not None
    assert second.event_type == "window_title_changed"
    metadata = json.loads(second.metadata_json or "{}")
    assert metadata["previous_window_title"] == "main.py"
    assert metadata["current_window_title"] == "other.py"


def test_window_watcher_throttles_title_change_events_per_app():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Code", "main.py", "com.microsoft.VSCode"),
                ("Code", "other.py", "com.microsoft.VSCode"),
                ("Code", "third.py", "com.microsoft.VSCode"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 1.0, 2.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()
        third = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is not None
    assert second.event_type == "window_title_changed"
    assert third is None


def test_window_watcher_suppresses_browser_title_changes():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Safari", "Page A", "com.apple.Safari"),
                ("Safari", "Page B", "com.apple.Safari"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 1.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is None


def test_window_watcher_emits_focus_on_app_switch():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Code", "main.py", "com.microsoft.VSCode"),
                ("Terminal", "shell", "com.apple.Terminal"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 1.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is not None
    assert second.event_type == "window_focus"
