"""Issue #28: persistent global pause and per-source switches.

All tests use a temp DB via init_db(tmp_path); nothing touches ~/.keypulse.
"""

from __future__ import annotations

import queue
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from keypulse.capture.capture_control import (
    disabled_sources,
    set_source_enabled,
    source_writes_allowed,
)
from keypulse.capture.manager import CaptureManager
from keypulse.capture.normalizer import (
    normalize_clipboard_event,
    normalize_keyboard_chunk_event,
    normalize_window_event,
)
from keypulse.capture.watchers.keyboard_chunk import KeyboardChunkWatcher
from keypulse.config import Config
from keypulse.sources import discover, registry
from keypulse.sources.cleaning.config import CleaningConfig
from keypulse.sources.scheduler import SourcesScheduler
from keypulse.sources.sink import persist_semantic_event
from keypulse.sources.types import SemanticEvent
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.repository import get_state, set_state


@pytest.fixture
def cfg(tmp_path):
    db = tmp_path / "kp.db"
    init_db(db)
    yield Config.model_validate(
        {"app": {"db_path": str(db), "flush_interval_sec": 1}, "privacy": {"camera_scene_pause": False}}
    )
    close()


class _FakeWatcher:
    HEARTBEAT_TIMEOUT_SEC = None

    def __init__(self, name: str) -> None:
        self.name = name
        self.paused = False

    def start(self) -> None:
        self.paused = False

    def stop(self) -> None:
        pass

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def is_heartbeat_dead(self) -> bool:
        return False

    def health(self) -> dict:
        return {"name": self.name, "paused": self.paused}


def _manager(cfg: Config, monkeypatch) -> CaptureManager:
    """Manager with fake watchers (no macOS APIs, no threads unless start() is called)."""
    manager = CaptureManager(cfg)

    def _install(self):
        for name in ("window", "clipboard", "keyboard_chunk"):
            self._watchers[name] = _FakeWatcher(name)

    monkeypatch.setattr(manager, "_init_watchers", lambda: _install(manager))
    manager._init_watchers()
    return manager


def _emit_three(manager: CaptureManager) -> None:
    """Simulate one event from each of three live watchers, through the real queue + drain path."""
    events = [
        normalize_window_event(
            event_type="window_focus_session",
            app_name="Notes",
            window_title="Roadmap",
            process_name="com.apple.Notes",
            metadata={"duration_sec": 12},
        ),
        normalize_clipboard_event(text="copied sentence about the roadmap"),
        normalize_keyboard_chunk_event(text="typed a short note today", app_name="Notes"),
    ]
    for event in events:
        manager._queue.put(event)
    manager._drain_queue()


def _rows(source: str) -> int:
    return int(get_conn().execute("SELECT COUNT(*) FROM raw_events WHERE source = ?", (source,)).fetchone()[0])


_SOURCES = ("window", "clipboard", "keyboard_chunk")


def test_pause_blocks_three_watchers_then_resume_writes_again(cfg, monkeypatch):
    manager = _manager(cfg, monkeypatch)
    manager.pause()

    _emit_three(manager)
    assert [_rows(s) for s in _SOURCES] == [0, 0, 0]

    manager.resume()
    _emit_three(manager)
    assert all(_rows(s) > 0 for s in _SOURCES)


def test_pause_persists_across_restart(cfg, monkeypatch):
    first = _manager(cfg, monkeypatch)
    first.pause()

    # Daemon restart: new manager instance, start() then stop() as app.py does.
    second = _manager(cfg, monkeypatch)
    second.start()
    second.stop()
    assert get_state("status") == "paused"  # start() and stop() must not overwrite a persisted pause

    third = _manager(cfg, monkeypatch)
    third._sync_control_state()
    assert third._paused.is_set()
    _emit_three(third)
    assert [_rows(s) for s in _SOURCES] == [0, 0, 0]

    third.resume()
    fourth = _manager(cfg, monkeypatch)
    fourth.start()
    fourth.stop()
    assert get_state("status") == "stopped"  # after resume, the pause is gone: start() ran as running


def test_disabled_keyboard_chunk_writes_nothing_others_still_write(cfg, monkeypatch):
    set_source_enabled("keyboard_chunk", False)
    manager = _manager(cfg, monkeypatch)

    _emit_three(manager)

    assert _rows("keyboard_chunk") == 0
    assert _rows("window") > 0
    assert _rows("clipboard") > 0


def test_disabled_source_survives_restart_and_watcher_is_quieted(cfg, monkeypatch):
    set_source_enabled("keyboard_chunk", False)
    first = _manager(cfg, monkeypatch)
    first._sync_source_switches()
    assert first._watchers["keyboard_chunk"].paused is True

    second = _manager(cfg, monkeypatch)  # restart
    second._sync_source_switches()
    assert disabled_sources() == frozenset({"keyboard_chunk"})
    assert second._watchers["keyboard_chunk"].paused is True
    _emit_three(second)
    assert _rows("keyboard_chunk") == 0

    set_source_enabled("keyboard_chunk", True)
    second._sync_source_switches()  # re-enable: watcher is resumed on the transition
    assert second._watchers["keyboard_chunk"].paused is False
    _emit_three(second)
    assert _rows("keyboard_chunk") > 0


def test_resume_does_not_unquiet_disabled_source(cfg, monkeypatch):
    set_source_enabled("keyboard_chunk", False)
    manager = _manager(cfg, monkeypatch)
    manager.pause()
    manager.resume()  # resume must not un-quiet a switched-off watcher
    assert manager._watchers["window"].paused is False
    assert manager._watchers["keyboard_chunk"].paused is True
    _emit_three(manager)
    assert _rows("keyboard_chunk") == 0
    assert _rows("window") > 0


def test_paused_keyboard_watcher_does_not_buffer_keystrokes():
    watcher = KeyboardChunkWatcher(queue.Queue())
    watcher.pause()
    watcher.record_input("should not be kept")
    assert watcher._buffer._parts == []

    watcher.resume()
    watcher.record_input("kept")
    assert watcher._buffer._parts == ["kept"]


def _semantic(source: str) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc),
        source=source,
        actor="user",
        intent="synthetic commit message",
        artifact="commit:abc1234",
        raw_ref=f"{source}:r1",
        privacy_tier="green",
        metadata={"repo_path": "/synthetic/repo"},
    )


def test_source_import_sink_writes_nothing_while_paused_or_disabled(cfg):
    assert persist_semantic_event(_semantic("git_log"), async_named_entities=False) > 0

    set_source_enabled("git_log", False)
    assert persist_semantic_event(_semantic("git_log"), async_named_entities=False) == 0
    assert _rows("git_log") == 1  # only the earlier row

    set_source_enabled("git_log", True)
    set_state("status", "paused")  # global pause blocks source imports too
    assert source_writes_allowed("git_log") is False
    assert persist_semantic_event(_semantic("git_log"), async_named_entities=False) == 0


def test_read_all_does_not_read_or_advance_cursor_for_disabled_plugin(cfg, monkeypatch):
    calls: list[str] = []

    class _Plugin:
        def __init__(self, name: str) -> None:
            self.name = name

        def read(self, instance, since, until):
            calls.append(self.name)
            return iter(())

    monkeypatch.setattr(registry, "discover_all", lambda source=None: {"git_log": [object()], "claude_code": [object()]})
    monkeypatch.setattr(registry, "_select_sources", lambda source: [_Plugin("git_log"), _Plugin("claude_code")])
    monkeypatch.setattr(registry, "load_cleaning_config", lambda: CleaningConfig())
    set_source_enabled("git_log", False)

    list(registry.read_all(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 10, 14, tzinfo=timezone.utc)))

    assert calls == ["claude_code"]


def test_scheduler_skips_scan_for_disabled_source_without_advancing_since(cfg):
    class _Executor:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit(self, fn, plugin_name, reason):
            self.submitted.append(plugin_name)

    scheduler = SourcesScheduler(list_sources_fn=lambda: [], read_all_fn=lambda *a, **k: iter(()))
    scheduler._running.set()
    executor = _Executor()
    scheduler._executor = executor

    set_source_enabled("git_log", False)
    scheduler._schedule_scan("git_log", reason="test")
    assert executor.submitted == []
    assert "git_log" not in scheduler._last_success_utc

    set_source_enabled("git_log", True)
    scheduler._schedule_scan("git_log", reason="test")
    assert executor.submitted == ["git_log"]


def test_sources_disable_enable_list_and_unknown_name(cfg, monkeypatch):
    monkeypatch.setattr(discover.Config, "load", classmethod(lambda cls: cfg))
    runner = CliRunner()

    result = runner.invoke(discover.sources_group, ["disable", "keyboard_chunk"])
    assert result.exit_code == 0, result.output
    assert disabled_sources() == frozenset({"keyboard_chunk"})

    listed = runner.invoke(discover.sources_group, ["list"])
    assert "keyboard_chunk\t[capture watcher]\t[off]" in listed.output

    result = runner.invoke(discover.sources_group, ["enable", "keyboard_chunk"])
    assert result.exit_code == 0, result.output
    assert disabled_sources() == frozenset()

    bad = runner.invoke(discover.sources_group, ["disable", "no_such_source"])
    assert bad.exit_code != 0
    assert disabled_sources() == frozenset()


def test_source_alias_is_registered_on_main_cli():
    from keypulse.cli import main

    assert "source" in main.commands
    assert "sources" in main.commands


def test_status_plain_exposes_pause_and_disabled_sources(cfg, monkeypatch):
    import keypulse.cli as cli

    class _NoDaemonLock:
        def __init__(self, *a, **k) -> None:
            pass

        def get_pid(self):
            return None

        def is_running(self) -> bool:
            return False

    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    monkeypatch.setattr(cli, "SingleInstanceLock", _NoDaemonLock)
    monkeypatch.setattr(cli, "_launchd_label_loaded", lambda label: False)
    set_source_enabled("keyboard_chunk", False)
    set_state("status", "paused")

    result = CliRunner().invoke(cli.main, ["status", "--plain"])

    assert result.exit_code == 0, result.output
    assert "paused=True" in result.output
    assert "disabled_sources=keyboard_chunk" in result.output
