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


def test_window_watcher_flushes_previous_session_on_app_switch():
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
    assert second.event_type == "window_focus_session"


def test_window_watcher_flushes_session_on_app_switch():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Code", "main.py", "com.microsoft.VSCode"),
                ("Terminal", "shell", "com.apple.Terminal"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 15.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is not None
    assert second.event_type == "window_focus_session"
    assert second.app_name == "Code"
    assert second.window_title == "main.py"
    assert second.ts_end is not None
    metadata = json.loads(second.metadata_json or "{}")
    assert metadata["duration_sec"] == 15
    assert metadata["reason"] == "app_switch"


def test_window_watcher_flushes_stable_title_after_interval():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[
                ("Code", "main.py", "com.microsoft.VSCode"),
                ("Code", "main.py", "com.microsoft.VSCode"),
            ],
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=[0.0, 181.0]),
    ):
        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.event_type == "window_focus"
    assert second is not None
    assert second.event_type == "window_focus_session"
    metadata = json.loads(second.metadata_json or "{}")
    assert metadata["duration_sec"] == 181
    assert metadata["reason"] == "title_stable"


def test_window_watcher_can_flush_current_session_for_idle_timeout():
    watcher = _make_watcher()

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            return_value=("Code", "main.py", "com.microsoft.VSCode"),
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", return_value=0.0),
    ):
        event = watcher.capture_once()

    assert event is not None
    flushed = watcher.flush_current_session(
        ended_at="2026-04-22T09:03:00+00:00",
        ended_at_mono=180.0,
        reason="idle_timeout",
    )

    assert flushed is not None
    assert flushed.event_type == "window_focus_session"
    metadata = json.loads(flushed.metadata_json or "{}")
    assert metadata["duration_sec"] == 180
    assert metadata["reason"] == "idle_timeout"


def test_window_watcher_emits_ten_session_events_for_thirty_minutes_of_stable_focus():
    watcher = _make_watcher()
    monotonic_values = [0.0] + [float(mark) for mark in range(180, 1801, 180)]

    with (
        patch(
            "keypulse.capture.watchers.window._get_frontmost_app",
            side_effect=[("Code", "main.py", "com.microsoft.VSCode")] * len(monotonic_values),
        ),
        patch("keypulse.capture.watchers.window.time.monotonic", side_effect=monotonic_values),
    ):
        events = [watcher.capture_once() for _ in monotonic_values]

    assert sum(1 for event in events if event and event.event_type == "window_focus_session") == 10
    assert sum(1 for event in events if event and event.event_type == "window_heartbeat") == 0
