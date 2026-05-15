from __future__ import annotations

import time

from keypulse.sources.scheduler import SourcesScheduler
from keypulse.sources.types import DataSource, DataSourceInstance


class _FakeSource(DataSource):
    privacy_tier = "green"

    def __init__(self, *, name: str, liveness: str, app_hints: tuple[str, ...] = ()) -> None:
        self.name = name
        self.liveness = liveness
        self.app_hints = app_hints

    def discover(self) -> list[DataSourceInstance]:
        return []

    def read(self, instance, since, until):
        _ = instance, since, until
        return iter(())


class _FakeWorkspaceEventSource:
    def __init__(self) -> None:
        self._on_launch = None
        self._on_quit = None
        self._on_unlock = None
        self.started = False
        self.stopped = False

    def start(self, *, on_app_launch, on_app_quit, on_unlock) -> None:
        self.started = True
        self._on_launch = on_app_launch
        self._on_quit = on_app_quit
        self._on_unlock = on_unlock

    def stop(self) -> None:
        self.stopped = True

    def emit_launch(self, *, bundle_id: str = "", process_name: str = "") -> None:
        if self._on_launch is not None:
            self._on_launch(bundle_id, process_name)

    def emit_quit(self, *, bundle_id: str = "", process_name: str = "") -> None:
        if self._on_quit is not None:
            self._on_quit(bundle_id, process_name)

    def emit_unlock(self) -> None:
        if self._on_unlock is not None:
            self._on_unlock()


def _wait_for(predicate, *, timeout: float = 1.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_scheduler_polls_always_plugins() -> None:
    calls: list[str] = []
    fake_event_source = _FakeWorkspaceEventSource()

    def fake_read_all(since, until, *, source, persist_raw_events=True):
        _ = since, until, persist_raw_events
        calls.append(source)
        return iter(())

    scheduler = SourcesScheduler(
        poll_interval_sec=0.05,
        debounce_sec=0,
        list_sources_fn=lambda: [_FakeSource(name="always_a", liveness="always")],
        read_all_fn=fake_read_all,
        event_source_factory=lambda: fake_event_source,
    )

    scheduler.start()
    try:
        assert _wait_for(lambda: len(calls) >= 2, timeout=0.5)
    finally:
        scheduler.stop()

    assert all(name == "always_a" for name in calls)


def test_scheduler_launch_event_triggers_app_running_only() -> None:
    calls: list[str] = []
    fake_event_source = _FakeWorkspaceEventSource()

    def fake_read_all(since, until, *, source, persist_raw_events=True):
        _ = since, until, persist_raw_events
        calls.append(source)
        return iter(())

    scheduler = SourcesScheduler(
        poll_interval_sec=60,
        debounce_sec=0,
        list_sources_fn=lambda: [
            _FakeSource(
                name="chrome_history",
                liveness="app_running",
                app_hints=("com.google.Chrome", "Google Chrome"),
            )
        ],
        read_all_fn=fake_read_all,
        event_source_factory=lambda: fake_event_source,
    )

    scheduler.start()
    try:
        fake_event_source.emit_launch(bundle_id="com.google.Chrome", process_name="Google Chrome")
        assert _wait_for(lambda: calls == ["chrome_history"], timeout=0.5)

        fake_event_source.emit_quit(bundle_id="com.google.Chrome", process_name="Google Chrome")
        time.sleep(0.05)
        assert calls == ["chrome_history"]
    finally:
        scheduler.stop()


def test_scheduler_debounce_blocks_repeated_triggers(monkeypatch) -> None:
    calls: list[str] = []
    fake_event_source = _FakeWorkspaceEventSource()
    clock = {"mono": 100.0}

    def fake_read_all(since, until, *, source, persist_raw_events=True):
        _ = since, until, persist_raw_events
        calls.append(source)
        return iter(())

    scheduler = SourcesScheduler(
        poll_interval_sec=60,
        debounce_sec=300,
        list_sources_fn=lambda: [
            _FakeSource(name="safari_history", liveness="app_running", app_hints=("com.apple.Safari", "Safari"))
        ],
        read_all_fn=fake_read_all,
        event_source_factory=lambda: fake_event_source,
        monotonic_fn=lambda: clock["mono"],
    )

    scheduler.start()
    try:
        fake_event_source.emit_launch(bundle_id="com.apple.Safari", process_name="Safari")
        assert _wait_for(lambda: calls == ["safari_history"], timeout=0.5)

        clock["mono"] += 10
        fake_event_source.emit_launch(bundle_id="com.apple.Safari", process_name="Safari")
        time.sleep(0.05)
        assert calls == ["safari_history"]

        clock["mono"] += 301
        fake_event_source.emit_launch(bundle_id="com.apple.Safari", process_name="Safari")
        assert _wait_for(lambda: calls == ["safari_history", "safari_history"], timeout=0.5)
    finally:
        scheduler.stop()


def test_scheduler_isolates_plugin_exceptions(monkeypatch) -> None:
    calls: list[str] = []
    logged_errors: list[str] = []
    fake_event_source = _FakeWorkspaceEventSource()

    def fake_read_all(since, until, *, source, persist_raw_events=True):
        _ = since, until, persist_raw_events
        calls.append(source)
        if source == "bad_plugin":
            raise RuntimeError("boom")
        return iter(())

    scheduler = SourcesScheduler(
        poll_interval_sec=60,
        debounce_sec=0,
        list_sources_fn=lambda: [
            _FakeSource(name="bad_plugin", liveness="after_unlock"),
            _FakeSource(name="good_plugin", liveness="after_unlock"),
        ],
        read_all_fn=fake_read_all,
        event_source_factory=lambda: fake_event_source,
    )
    monkeypatch.setattr(
        "keypulse.sources.scheduler.LOGGER.exception",
        lambda msg, *args, **kwargs: logged_errors.append(msg % args if args else str(msg)),
    )

    scheduler.start()
    try:
        fake_event_source.emit_unlock()
        assert _wait_for(lambda: set(calls) == {"bad_plugin", "good_plugin"}, timeout=0.5)
    finally:
        scheduler.stop()

    assert any("sources scheduler scan failed plugin=bad_plugin" in line for line in logged_errors)


def test_scheduler_quit_and_unlock_events_trigger_expected_plugins() -> None:
    calls: list[str] = []
    fake_event_source = _FakeWorkspaceEventSource()

    def fake_read_all(since, until, *, source, persist_raw_events=True):
        _ = since, until, persist_raw_events
        calls.append(source)
        return iter(())

    scheduler = SourcesScheduler(
        poll_interval_sec=60,
        debounce_sec=0,
        list_sources_fn=lambda: [
            _FakeSource(name="leveldb_reader", liveness="after_app_quit", app_hints=("Cursor", "com.todesktop.230313mzl4w4u92")),
            _FakeSource(name="wechat", liveness="after_unlock"),
        ],
        read_all_fn=fake_read_all,
        event_source_factory=lambda: fake_event_source,
    )

    scheduler.start()
    try:
        fake_event_source.emit_launch(bundle_id="com.todesktop.230313mzl4w4u92", process_name="Cursor")
        time.sleep(0.05)
        assert calls == []

        fake_event_source.emit_quit(bundle_id="com.todesktop.230313mzl4w4u92", process_name="Cursor")
        assert _wait_for(lambda: "leveldb_reader" in calls, timeout=0.5)

        fake_event_source.emit_unlock()
        assert _wait_for(lambda: "wechat" in calls, timeout=0.5)
    finally:
        scheduler.stop()
