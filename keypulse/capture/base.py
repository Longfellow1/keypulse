from __future__ import annotations
import abc
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional
from keypulse.store.models import RawEvent


class BaseWatcher(abc.ABC):
    """Abstract base class for all event watchers."""

    name: str = "base"

    def __init__(self, event_queue: queue.Queue):
        self._queue = event_queue
        self._running = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"watcher-{self.name}")
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
        return {"name": self.name, "running": self.is_running(), "paused": self._paused.is_set()}

    def emit(self, event: RawEvent):
        """Put event onto shared queue."""
        self._queue.put(event)

    @abc.abstractmethod
    def _run(self):
        """Main watcher loop. Must check self._running.is_set() and self._paused.is_set()."""
        ...
