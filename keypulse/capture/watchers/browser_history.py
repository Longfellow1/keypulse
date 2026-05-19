from __future__ import annotations

import queue
import time

from keypulse.capture.base import BaseWatcher


DEFAULT_HISTORY_BROWSERS = (
    "Safari",
    "Google Chrome",
    "Arc",
    "Microsoft Edge",
    "Brave Browser",
    "Firefox",
)


class BrowserHistoryWatcher(BaseWatcher):
    """P2.2 framework placeholder.

    Real sqlite incremental read/copy logic will be added in the next milestone.
    """

    name = "browser_history"
    HEARTBEAT_TIMEOUT_SEC = 900.0

    def __init__(
        self,
        event_queue: queue.Queue,
        *,
        poll_interval_sec: float = 300.0,
        enabled: bool = False,
        copy_to_cache: bool = True,
        browsers: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(event_queue)
        self._poll_interval = max(float(poll_interval_sec), 1.0)
        self._enabled = bool(enabled)
        self._copy_to_cache = bool(copy_to_cache)
        self._browsers = tuple(browsers or DEFAULT_HISTORY_BROWSERS)

    def _run(self) -> None:
        while self._running.is_set():
            self.beat()
            time.sleep(self._poll_interval)
