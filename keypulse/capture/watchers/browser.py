from __future__ import annotations
import queue
import time
from keypulse.capture.base import BaseWatcher
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.browser")


class BrowserWatcher(BaseWatcher):
    """Placeholder — browser watcher not implemented in MVP."""
    name = "browser"

    def _run(self):
        logger.info("BrowserWatcher: not implemented in MVP, exiting.")
        return
