from __future__ import annotations
import abc
import queue
import threading
import time
from typing import Optional
from keypulse.store.models import RawEvent
from keypulse.utils.logging import get_logger


_LOGGER = get_logger("capture.watcher")


class BaseWatcher(abc.ABC):
    """Abstract base class for all event watchers.

    The thread launched by ``start()`` runs ``_supervised_run`` rather than
    ``_run`` directly, so a crashing ``_run`` is restarted with exponential
    backoff up to ``MAX_CRASHES``. A normal return from ``_run`` (e.g.
    triggered by ``stop()`` clearing ``_running``) is treated as graceful
    shutdown and is not retried.

    Subclasses can opt into heartbeat supervision by setting
    ``HEARTBEAT_TIMEOUT_SEC``. CaptureManager polls every watcher and calls
    ``heartbeat_revive()`` when the watcher hasn't emitted within the
    timeout — this catches "silent stuck" cases where the thread is alive
    but macOS has revoked permission or the underlying source went mute.
    """

    name: str = "base"

    INITIAL_BACKOFF_SEC: float = 1.0
    MAX_BACKOFF_SEC: float = 30.0
    MAX_CRASHES: int = 10

    # Heartbeat supervision (opt-in). None disables the check.
    HEARTBEAT_TIMEOUT_SEC: Optional[float] = None
    MAX_HEARTBEAT_REVIVALS: int = 5

    def __init__(self, event_queue: queue.Queue):
        self._queue = event_queue
        self._running = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._crash_count: int = 0
        self._last_error: Optional[str] = None
        self._gave_up: bool = False
        self._started_at_mono: Optional[float] = None
        self._last_emit_at_mono: Optional[float] = None
        self._heartbeat_revival_count: int = 0
        self._heartbeat_gave_up: bool = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._paused.clear()
        self._crash_count = 0
        self._last_error = None
        self._gave_up = False
        self._started_at_mono = time.monotonic()
        self._last_emit_at_mono = None
        self._thread = threading.Thread(
            target=self._supervised_run, daemon=True, name=f"watcher-{self.name}"
        )
        self._thread.start()

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def health(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running(),
            "paused": self._paused.is_set(),
            "crashes": self._crash_count,
            "last_error": self._last_error,
            "gave_up": self._gave_up,
            "heartbeat_revivals": self._heartbeat_revival_count,
            "heartbeat_gave_up": self._heartbeat_gave_up,
        }

    def emit(self, event: RawEvent):
        """Put event onto shared queue."""
        self._last_emit_at_mono = time.monotonic()
        self._queue.put(event)

    def beat(self) -> None:
        """Signal liveness without emitting an event.

        For poll-based watchers where 'no event emitted' is normal behavior
        (clipboard idle, browser history quiet) — call this once per loop
        iteration to distinguish 'alive but quiet' from 'silently stuck'.
        """
        self._last_emit_at_mono = time.monotonic()

    def is_heartbeat_dead(self, now_mono: Optional[float] = None) -> bool:
        """Return True if this watcher should be considered silently stuck.

        Returns False (not dead) when:
          - Heartbeat is disabled (HEARTBEAT_TIMEOUT_SEC is None)
          - Watcher isn't running, or is paused, or already gave up
          - Has never been started (no reference timestamp)
        Otherwise compares (now - last_emit_or_start) against the timeout.
        """
        if self.HEARTBEAT_TIMEOUT_SEC is None:
            return False
        if not self.is_running() or self._paused.is_set() or self._heartbeat_gave_up:
            return False
        reference = self._last_emit_at_mono if self._last_emit_at_mono is not None else self._started_at_mono
        if reference is None:
            return False
        if now_mono is None:
            now_mono = time.monotonic()
        return (now_mono - reference) > self.HEARTBEAT_TIMEOUT_SEC

    def heartbeat_revive(self) -> bool:
        """Stop and restart the watcher to recover from silent stuck state.

        Returns True if the watcher was restarted, False if the revival
        budget is exhausted (sets ``_heartbeat_gave_up``).
        """
        if self._heartbeat_revival_count >= self.MAX_HEARTBEAT_REVIVALS:
            if not self._heartbeat_gave_up:
                self._heartbeat_gave_up = True
                _LOGGER.error(
                    "watcher %s exceeded heartbeat revival budget (%d), giving up",
                    self.name, self.MAX_HEARTBEAT_REVIVALS,
                )
            return False
        self._heartbeat_revival_count += 1
        _LOGGER.error(
            "watcher %s heartbeat dead (no emit in %.0fs), reviving (#%d/%d)",
            self.name,
            self.HEARTBEAT_TIMEOUT_SEC or 0.0,
            self._heartbeat_revival_count,
            self.MAX_HEARTBEAT_REVIVALS,
        )
        self.stop()
        self.start()
        return True

    def _supervised_run(self) -> None:
        """Restart ``_run`` with exponential backoff when it raises.

        Exits cleanly when:
          - ``_run`` returns normally (graceful shutdown)
          - ``_running`` is cleared (stop() called)
          - crash budget exceeded (sets ``_gave_up`` so health() can report it)
        """
        backoff = self.INITIAL_BACKOFF_SEC
        while self._running.is_set():
            try:
                self._run()
                return
            except Exception as exc:  # pragma: no cover - real watchers raise here
                self._crash_count += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                _LOGGER.exception(
                    "watcher %s crashed (#%d/%d), backoff %.1fs",
                    self.name, self._crash_count, self.MAX_CRASHES, backoff,
                )
                if self._crash_count >= self.MAX_CRASHES:
                    self._gave_up = True
                    _LOGGER.error(
                        "watcher %s exceeded crash budget (%d), giving up",
                        self.name, self.MAX_CRASHES,
                    )
                    return
                # Sleep in small slices so stop() interrupts promptly.
                deadline = time.monotonic() + backoff
                while self._running.is_set() and time.monotonic() < deadline:
                    time.sleep(0.1)
                backoff = min(backoff * 2.0, self.MAX_BACKOFF_SEC)

    @abc.abstractmethod
    def _run(self):
        """Main watcher loop. Must check self._running.is_set() and self._paused.is_set()."""
        ...
