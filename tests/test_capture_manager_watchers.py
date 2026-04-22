from __future__ import annotations

import json

from keypulse.capture.manager import CaptureManager
from keypulse.capture.normalizer import normalize_ax_text_event
from keypulse.capture.normalizer import normalize_window_event
from keypulse.config import Config


def test_manager_wires_light_capture_watchers_only_when_enabled():
    config = Config.model_validate(
        {
            "watchers": {
                "window": False,
                "idle": False,
                "clipboard": False,
                "manual": False,
                "browser": False,
                "ax_text": True,
                "keyboard_chunk": True,
                "ocr": True,
            }
        }
    )

    manager = CaptureManager(config)

    manager._init_watchers()

    assert set(manager._watchers) == {"ax_text", "keyboard_chunk", "ocr"}


def test_manager_wires_browser_watcher_when_enabled():
    config = Config.model_validate(
        {
            "watchers": {
                "window": False,
                "idle": False,
                "clipboard": False,
                "manual": False,
                "browser": True,
                "ax_text": False,
                "keyboard_chunk": False,
                "ocr": False,
            }
        }
    )

    manager = CaptureManager(config)

    manager._init_watchers()

    assert set(manager._watchers) == {"browser"}


class _StubKeyboardWatcher:
    def __init__(self):
        self.calls = []

    def record_input(self, **kwargs):
        self.calls.append(kwargs)


class _StubOCRWatcher:
    def __init__(self):
        self.keyboard_calls = []
        self.capture_calls = []

    def note_keyboard_activity(self, now=None):
        self.keyboard_calls.append(now)

    def capture_once(self, **kwargs):
        self.capture_calls.append(kwargs)
        from keypulse.capture.normalizer import normalize_ocr_text_event

        return normalize_ocr_text_event(
            text="ocr from manager",
            metadata={"provider": "stub"},
        )


class _StubWindowAwareOCRWatcher:
    def __init__(self):
        self.window_calls = []

    def note_window_change(self, now=None):
        self.window_calls.append(now)


def test_manager_can_feed_keyboard_input_to_watchers():
    manager = CaptureManager(Config())
    keyboard = _StubKeyboardWatcher()
    ocr = _StubOCRWatcher()
    manager._watchers["keyboard_chunk"] = keyboard
    manager._watchers["ocr"] = ocr

    manager.record_keyboard_input(
        text="abc",
        app_name="Notes",
        window_title="Draft",
        process_name="com.apple.Notes",
        now=1.5,
    )

    assert keyboard.calls == [
        {
            "text": "abc",
            "app_name": "Notes",
            "window_title": "Draft",
            "process_name": "com.apple.Notes",
            "now": 1.5,
        }
    ]
    assert ocr.keyboard_calls == [1.5]


def test_manager_notifies_ocr_on_window_title_change():
    manager = CaptureManager(Config())
    ocr = _StubWindowAwareOCRWatcher()
    manager._watchers["ocr"] = ocr

    manager._feed_light_capture_watchers(
        normalize_window_event(
            event_type="window_title_changed",
            app_name="Code",
            window_title="main.py",
            process_name="com.microsoft.VSCode",
        )
    )

    assert ocr.window_calls == [None]


def test_manager_can_trigger_ocr_from_explicit_image():
    manager = CaptureManager(Config())
    ocr = _StubOCRWatcher()
    manager._watchers["ocr"] = ocr

    event = manager.capture_ocr_image(
        image_ref="/tmp/input.png",
        now=3.0,
        app_name="Preview",
        window_title="Scan",
        process_name="com.apple.Preview",
        content_signature="sig-1",
    )

    assert event is not None
    assert event.content_text == "ocr from manager"
    assert ocr.capture_calls[0]["image_ref"] == "/tmp/input.png"
    context = ocr.capture_calls[0]["context"]
    assert context.app_name == "Preview"
    assert context.window_title == "Scan"
    assert context.process_name == "com.apple.Preview"
    assert context.content_signature == "sig-1"
    metadata = json.loads(event.metadata_json)
    assert metadata["provider"] == "stub"


def test_manager_runtime_snapshot_tracks_multi_source_counts():
    manager = CaptureManager(Config())
    manager._watchers = {}

    manager._record_runtime_event(
        normalize_ax_text_event(
            text="hello",
            app_name="Notes",
            window_title="Draft",
            process_name="com.apple.Notes",
        )
    )

    snapshot = manager._runtime_snapshot()

    assert snapshot["multi_source_counts"]["ax_text"] == 1
    assert snapshot["multi_source_counts"]["ocr_text"] == 0
    assert snapshot["last_seen"]["ax_text"]
    assert snapshot["pid"] > 0
    assert snapshot["host_executable"]


def test_manager_persist_runtime_state_writes_json_snapshot(monkeypatch):
    manager = CaptureManager(Config())
    manager._watchers = {}
    writes: list[tuple[str, str]] = []

    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda key, value: writes.append((key, value)))

    manager._persist_runtime_state(force=True)

    assert writes
    key, value = writes[-1]
    assert key == "capture_runtime"
    snapshot = json.loads(value)
    assert snapshot["multi_source_counts"]["keyboard_chunk"] == 0
