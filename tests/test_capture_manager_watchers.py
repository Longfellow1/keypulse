from __future__ import annotations

from penpal.capture.manager import CaptureManager
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


def test_manager_core_progress_is_written_to_atomic_runtime_record(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from penpal import __version__
    from penpal.foundation.runtime_record import Progress, RuntimeRecord, read_record

    monkeypatch.setenv("HOME", str(tmp_path))
    manager = CaptureManager(Config())
    manager._watchers = {}
    started_at = datetime.now(timezone.utc).isoformat()
    manager._runtime_record = RuntimeRecord(
        generation="test-generation", pid=123, process_start_time="test-start",
        version=__version__, code_fingerprint="test-fingerprint", started_at=started_at,
        written_at=started_at, progress={"core_loop": Progress(0, started_at)},
    )

    manager.report_progress("core_loop")
    manager._write_runtime_record(force=True)

    record = read_record()
    assert record is not None and record.generation == "test-generation"
    assert record.progress["core_loop"].seq == 1
    assert record.components["storage"].state == "running"


def test_runtime_component_does_not_persist_watcher_exception_text(monkeypatch):
    manager = CaptureManager(Config())
    manager._watchers["private_test"] = type("Watcher", (), {
        "health": lambda self: {
            "running": False, "paused": False, "gave_up": True,
            "heartbeat_gave_up": False, "last_error": "RuntimeError: private window title",
        },
    })()
    monkeypatch.setattr("penpal.capture.manager_runtime.disabled_sources", lambda: frozenset())
    monkeypatch.setattr("penpal.capture.manager_runtime.is_capture_paused", lambda: False)
    monkeypatch.setattr("penpal.capture.manager_runtime.get_state", lambda _key: "")

    component = manager._component_snapshot()["private_test"]

    assert component.state == "gave_up"
    assert component.detail == "crash_retries_exhausted"
    assert "private window title" not in component.detail


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
    monkeypatch.setattr("penpal.capture.manager_events.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager_events.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager_events.set_state", lambda *_args, **_kwargs: None)

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
    monkeypatch.setattr("penpal.capture.manager_events.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager_events.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager_events.set_state", lambda *_args, **_kwargs: None)

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
    monkeypatch.setattr("penpal.capture.manager_events.insert_raw_event", lambda event: captured.append(event) or 1)
    monkeypatch.setattr("penpal.capture.manager_events.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("penpal.capture.manager_events.set_state", lambda *_args, **_kwargs: None)

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
