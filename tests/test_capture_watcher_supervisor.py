from __future__ import annotations

import queue
import threading
import time

from keypulse.capture.base import BaseWatcher


class _CountingWatcher(BaseWatcher):
    """Test fixture: _run executes a script of behaviours per attempt."""

    name = "test"
    INITIAL_BACKOFF_SEC = 0.01  # keep tests fast
    MAX_BACKOFF_SEC = 0.05
    MAX_CRASHES = 3

    def __init__(self, q: queue.Queue, behaviors: list):
        super().__init__(q)
        self._behaviors = behaviors
        self.call_count = 0
        self.entered = threading.Event()
        self.exited = threading.Event()

    def _run(self):
        self.call_count += 1
        self.entered.set()
        try:
            behavior = self._behaviors[min(self.call_count - 1, len(self._behaviors) - 1)]
            if isinstance(behavior, Exception):
                raise behavior
            if callable(behavior):
                behavior(self)
        finally:
            self.exited.set()


def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_normal_return_does_not_restart() -> None:
    """Graceful exit (no exception) means the watcher is done; no restart."""
    q: queue.Queue = queue.Queue()
    w = _CountingWatcher(q, behaviors=[lambda self: None])
    w.start()

    assert _wait(lambda: not w.is_running())
    assert w.call_count == 1
    assert w._crash_count == 0
    assert w._gave_up is False


def test_one_crash_then_clean_exit_restarts_once() -> None:
    """First call raises, second returns cleanly. Should be called twice."""
    q: queue.Queue = queue.Queue()
    w = _CountingWatcher(
        q,
        behaviors=[
            RuntimeError("boom #1"),
            lambda self: None,
        ],
    )
    w.start()

    assert _wait(lambda: w.call_count >= 2 and not w.is_running())
    assert w.call_count == 2
    assert w._crash_count == 1
    assert w._last_error is not None and "boom #1" in w._last_error
    assert w._gave_up is False
    health = w.health()
    assert health["crashes"] == 1
    assert health["gave_up"] is False


def test_persistent_crashes_give_up_after_max() -> None:
    """Always-raising _run hits crash budget and the supervisor stops."""
    q: queue.Queue = queue.Queue()
    w = _CountingWatcher(q, behaviors=[RuntimeError("always")])
    w.start()

    assert _wait(lambda: w._gave_up, timeout=3.0)
    assert _wait(lambda: not w.is_running())
    assert w._crash_count == w.MAX_CRASHES
    assert w._gave_up is True
    health = w.health()
    assert health["crashes"] == w.MAX_CRASHES
    assert health["gave_up"] is True


def test_stop_interrupts_backoff_promptly() -> None:
    """stop() during the backoff sleep should exit within ~one slice."""

    class _SlowBackoffWatcher(_CountingWatcher):
        INITIAL_BACKOFF_SEC = 5.0  # would sleep a long time
        MAX_BACKOFF_SEC = 5.0
        MAX_CRASHES = 10

    q: queue.Queue = queue.Queue()
    w = _SlowBackoffWatcher(q, behaviors=[RuntimeError("crash")])
    w.start()

    assert _wait(lambda: w._crash_count >= 1, timeout=2.0)
    started = time.monotonic()
    w.stop()
    elapsed = time.monotonic() - started

    assert not w.is_running()
    # We use 0.1s slices internally; allow generous margin for CI.
    assert elapsed < 1.0, f"stop() took {elapsed:.2f}s — backoff did not interrupt"
