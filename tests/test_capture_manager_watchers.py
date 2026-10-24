from __future__ import annotations

import json

from penpal.capture.manager import CaptureManager
from penpal.capture.normalizer import normalize_ax_text_event
from penpal.capture.normalizer import normalize_keyboard_chunk_event
from penpal.capture.normalizer import normalize_manual_event
from penpal.config import Config


def test_manager_wires_keyboard_chunk_watcher_when_enabled():
    config = Config.model_validate(
        {
            "watchers": {
                "window": False,
                "idle": False,
                "clipboard": False,
                "manual": False,
                "keyboard_chunk": True,
                "browser": False,
                "ax_text": False,
            }
        }
    )

    manager = CaptureManager(config)

    manager._init_watchers()

    assert set(manager._watchers) == {"keyboard_chunk"}


def test_manager_wires_browser_watcher_when_enabled():
    config = Config.model_validate(
        {
            "watchers": {
                "window": False,
                "idle": False,
                "clipboard": False,
                "manual": False,
                "keyboard_chunk": False,
                "browser": True,
                "ax_text": False,
            }
        }
    )

    manager = CaptureManager(config)

    manager._init_watchers()

    assert set(manager._watchers) == {"browser"}


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

    monkeypatch.setattr("penpal.capture.manager.set_state", lambda key, value: writes.append((key, value)))

    manager._persist_runtime_state(force=True)

    assert writes
    key, value = writes[-1]
    assert key == "capture_runtime"
    snapshot = json.loads(value)
    assert snapshot["multi_source_counts"]["ax_text"] == 0


class _StubWindowSessionWatcher:
    def __init__(self, session_id: str):
        self._session_id = session_id

    def current_session_id(self):
        return self._session_id


def test_manager_attaches_current_window_session_id_to_non_window_events(monkeypatch):
    manager = CaptureManager(Config())
    manager._watchers["window"] = _StubWindowSessionWatcher("session-123")
    captured = []

    monkeypatch.setattr(manager._policy, "apply", lambda event: event)
    monkeypatch.setattr(manager._aggregator, "process", lambda event: None)
    monkeypatch.setattr("penpal.capture.manager.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(
        normalize_manual_event(
            text="record the implementation notes",
            app_name="Code",
            window_title="window watcher",
            ts_start="2026-04-22T09:10:00+00:00",
        )
    )

    assert captured
    assert captured[0].session_id == "session-123"


def test_manager_redacts_keyboard_chunk_content_before_persisting(monkeypatch):
    manager = CaptureManager(Config())
    captured = []

    monkeypatch.setattr(manager._policy, "apply", lambda event: event)
    monkeypatch.setattr(manager._aggregator, "process", lambda event: None)
    monkeypatch.setattr("penpal.capture.manager.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(
        normalize_manual_event(
            text="email alice@example.com phone 13800138000 token sk-ant-abcdefghijklmnopqrst1234567890",
            app_name="Claude",
            window_title="Draft",
            ts_start="2026-04-22T09:10:00+00:00",
        )
    )

    assert captured
    assert "[REDACTED]" in (captured[0].content_text or "")


def test_manager_keeps_keyboard_chunk_weight_high_for_chat_style_apps(monkeypatch):
    manager = CaptureManager(Config())
    captured = []

    monkeypatch.setattr(manager._policy, "apply", lambda event: event)
    monkeypatch.setattr(manager._aggregator, "process", lambda event: None)
    monkeypatch.setattr("penpal.capture.manager.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(
        normalize_keyboard_chunk_event(
            text="hello",
            app_name="Claude",
            window_title="Draft",
            process_name="com.anthropic.claude",
            ts_start="2026-04-22T09:10:00+00:00",
        )
    )

    assert captured
    assert captured[0].semantic_weight == 1.0
