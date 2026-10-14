"""One collector crashes: the others keep running, and the crashed one shows status "warning"
(error type only, never its message) in the health output the HUD reads."""
from __future__ import annotations

import json
import queue
import time

from keypulse.capabilities.builtin import _watcher_health
from keypulse.capabilities.builtin._watcher_health import WatcherHealthCapability, WatcherHealthSpec
from keypulse.capture.base import BaseWatcher, watcher_status

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


def test_crashed_collector_does_not_stop_others_and_reports_warning(monkeypatch) -> None:
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

        runtime = {"watchers": {"flaky_test": flaky.health(), "steady_test": steady.health()}}
        monkeypatch.setattr(
            "keypulse.capabilities.builtin._watcher_health.get_state",
            lambda _key: json.dumps(runtime, ensure_ascii=False),
        )
        flaky_cap = WatcherHealthCapability(
            WatcherHealthSpec(watcher_name="flaky_test", capability_name="flaky_watcher")
        )
        steady_cap = WatcherHealthCapability(
            WatcherHealthSpec(watcher_name="steady_test", capability_name="steady_watcher")
        )

        state = flaky_cap.monitor()
        assert not state.ok
        assert state.code == "flaky_test_watcher_crashed"
        signal = flaky_cap.diagnose(state)
        assert signal.level == "warn"
        assert "RuntimeError" in (signal.hint or "")
        assert "secret" not in (signal.hint or "") and "1234" not in (signal.hint or "")

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
        runtime = {"watchers": {"steady_test": steady.health()}}
        monkeypatch.setattr(_watcher_health, "get_state", lambda _key: json.dumps(runtime))
        cap = WatcherHealthCapability(WatcherHealthSpec(watcher_name="steady_test", capability_name="steady_watcher"))
        assert cap.monitor().ok
    finally:
        steady.stop()
