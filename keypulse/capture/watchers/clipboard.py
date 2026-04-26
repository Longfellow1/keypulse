from __future__ import annotations
import hashlib
import queue
import time
from datetime import datetime, timezone
from typing import Optional

from keypulse.capture.active_app import get_active_app
from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_clipboard_event
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.clipboard")

POLL_INTERVAL = 1.0  # seconds


class ClipboardWatcher(BaseWatcher):
    name = "clipboard"

    def __init__(self, event_queue: queue.Queue, max_text_length: int = 2000, dedup_window_sec: int = 600):
        super().__init__(event_queue)
        self._max_len = max_text_length
        self._dedup_window = dedup_window_sec
        self._last_count: Optional[int] = None
        self._recent_hashes: dict[str, float] = {}  # hash → timestamp

    def _run(self):
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString
        except ImportError:
            logger.error("pyobjc-framework-AppKit not available")
            return

        pb = NSPasteboard.generalPasteboard()
        self._last_count = pb.changeCount()

        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(POLL_INTERVAL)
                continue
            try:
                count = pb.changeCount()
                if count != self._last_count:
                    self._last_count = count
                    text = pb.stringForType_(NSPasteboardTypeString)
                    if text and isinstance(text, str):
                        self._handle_copy(text)
            except Exception as e:
                logger.error(f"ClipboardWatcher error: {e}")
            time.sleep(POLL_INTERVAL)

    def _handle_copy(self, text: str):
        now = time.monotonic()
        # Clean up old dedup entries
        self._recent_hashes = {
            h: t for h, t in self._recent_hashes.items()
            if now - t < self._dedup_window
        }
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        if h in self._recent_hashes:
            logger.debug("Clipboard dedup hit, skipping")
            return
        self._recent_hashes[h] = now

        # Truncate before emitting (further desensitization happens in manager)
        if len(text) > self._max_len:
            text = text[:self._max_len] + "...[truncated]"

        app_name, _ = get_active_app()
        ts = datetime.now(timezone.utc).isoformat()
        event = normalize_clipboard_event(text=text, app_name=app_name, ts_start=ts)
        self.emit(event)
        logger.debug(f"Clipboard captured ({len(text)} chars) from {app_name}")
