from __future__ import annotations
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_window_event
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.window")

HEARTBEAT_INTERVAL = 30  # seconds


def _get_frontmost_app() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (app_name, window_title, process_name). Requires Accessibility permission."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return None, None, None
        app_name = app.localizedName()
        process_name = app.bundleIdentifier() or app_name
        pid = app.processIdentifier()
        window_title = _get_window_title(pid)
        return app_name, window_title, process_name
    except Exception as e:
        logger.debug(f"get_frontmost_app error: {e}")
        return None, None, None


def _get_window_title(pid: int) -> Optional[str]:
    """Get focused window title via Accessibility API."""
    try:
        import ApplicationServices as AS
        app_elem = AS.AXUIElementCreateApplication(pid)
        err, window = AS.AXUIElementCopyAttributeValue(app_elem, "AXFocusedWindow", None)
        if err != 0 or not window:
            return None
        err, title = AS.AXUIElementCopyAttributeValue(window, "AXTitle", None)
        return title if err == 0 else None
    except Exception:
        return None


class WindowWatcher(BaseWatcher):
    name = "window"

    def __init__(self, event_queue: queue.Queue):
        super().__init__(event_queue)
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_heartbeat: float = 0.0

    def _run(self):
        """
        Poll for frontmost app changes every 1 second.
        Emit window_focus on app/title change, window_heartbeat every 30s.
        """
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(1)
                continue
            try:
                app_name, window_title, process_name = _get_frontmost_app()
                now = datetime.now(timezone.utc).isoformat()
                now_mono = time.monotonic()

                changed = (app_name != self._last_app) or (window_title != self._last_title)

                if changed and app_name:
                    event = normalize_window_event(
                        event_type="window_focus",
                        app_name=app_name,
                        window_title=window_title,
                        process_name=process_name,
                        ts_start=now,
                    )
                    self.emit(event)
                    self._last_app = app_name
                    self._last_title = window_title
                    self._last_heartbeat = now_mono

                elif (now_mono - self._last_heartbeat) >= HEARTBEAT_INTERVAL and app_name:
                    event = normalize_window_event(
                        event_type="window_heartbeat",
                        app_name=app_name,
                        window_title=window_title,
                        process_name=process_name,
                        ts_start=now,
                    )
                    self.emit(event)
                    self._last_heartbeat = now_mono

            except Exception as e:
                logger.error(f"WindowWatcher error: {e}")

            time.sleep(1)
