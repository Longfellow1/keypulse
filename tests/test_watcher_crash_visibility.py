"""A recovered watcher is healthy in the shared runtime record; other watchers keep running."""
from __future__ import annotations

import queue
import time

from penpal.capabilities.builtin import _watcher_health
from penpal.capabilities.builtin._watcher_health import WatcherHealthCapability, WatcherHealthSpec
from penpal.capture.base import BaseWatcher, watcher_status
from penpal.runtime import RuntimeState, RuntimeStatus
from penpal.foundation.runtime_record import Component

SECRET = "secret window title 工资 1234"


class _FlakyWatcher(BaseWatcher):
    name = "flaky_test"
    INITIAL_BACKOFF_SEC = 0.01
    MAX_BACKOFF_SEC = 0.02
    MAX_CRASHES = 5

    def __init__(self, q):
        super().__init__(q)
        self.runs = 0

    def _run(self):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError(SECRET)
        while self._running.is_set():
            self.emit("flaky-event")
            time.sleep(0.01)


class _SteadyWatcher(BaseWatcher):
    name = "steady_test"

    def __init__(self, q):
        super().__init__(q)
        self.emitted = 0

    def _run(self):
        while self._running.is_set():
            self.emit("steady-event")
            self.emitted += 1
            time.sleep(0.01)


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_recovered_collector_does_not_stop_others_or_report_failure(monkeypatch) -> None:
    q: queue.Queue = queue.Queue()
    flaky = _FlakyWatcher(q)
    steady = _SteadyWatcher(q)
    flaky.start()
    steady.start()
    try:
        assert _wait(lambda: flaky.runs >= 2 and flaky.is_running())  # crashed once, supervisor restarted it
        assert _wait(lambda: steady.emitted >= 20)
        assert steady.is_running(), "a crash in one collector must not take down another"

        assert flaky.health()["status"] == "warning"
        assert steady.health()["status"] == "ok"
        assert watcher_status(crashes=0, gave_up=True, heartbeat_gave_up=False) == "error"

        status = RuntimeStatus(RuntimeState.RUNNING, components={
            "flaky_test": Component("running"), "steady_test": Component("running"),
        })
        monkeypatch.setattr(_watcher_health, "evaluate", lambda: status)
        flaky_cap = WatcherHealthCapability(
            WatcherHealthSpec(watcher_name="flaky_test", capability_name="flaky_watcher")
        )
        steady_cap = WatcherHealthCapability(
            WatcherHealthSpec(watcher_name="steady_test", capability_name="steady_watcher")
        )

        assert flaky_cap.monitor().ok  # it recovered and is running; past exception text is not runtime state
        assert steady_cap.monitor().ok
    finally:
        flaky.stop()
        steady.stop()


def test_clean_watcher_is_ok_status_and_no_capability_failure(monkeypatch) -> None:
    q: queue.Queue = queue.Queue()
    steady = _SteadyWatcher(q)
    steady.start()
    try:
        assert _wait(lambda: steady.emitted >= 2)
        monkeypatch.setattr(
            _watcher_health,
            "evaluate",
            lambda: RuntimeStatus(RuntimeState.RUNNING, components={"steady_test": Component("running")}),
        )
        cap = WatcherHealthCapability(WatcherHealthSpec(watcher_name="steady_test", capability_name="steady_watcher"))
        assert cap.monitor().ok
    finally:
        steady.stop()
