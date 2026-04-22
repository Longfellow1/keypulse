from __future__ import annotations
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import (
    WINDOW_FOCUS_EVENT,
    WINDOW_HEARTBEAT_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
    normalize_window_event,
)
from keypulse.capture.watchers.browser import DEFAULT_SUPPORTED_BROWSERS
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

    def __init__(
        self,
        event_queue: queue.Queue,
        browser_app_names: list[str] | tuple[str, ...] | None = None,
    ):
        super().__init__(event_queue)
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_heartbeat: float = 0.0
        self._last_title_change_emit_at: dict[str, float] = {}
        self._browser_app_names = tuple(browser_app_names or DEFAULT_SUPPORTED_BROWSERS)

    def capture_once(self):
        try:
            app_name, window_title, process_name = _get_frontmost_app()
            if not app_name:
                return None

            now = datetime.now(timezone.utc).isoformat()
            now_mono = time.monotonic()
            changed_app = app_name != self._last_app
            changed_title = window_title != self._last_title

            if changed_app:
                event = normalize_window_event(
                    event_type=WINDOW_FOCUS_EVENT,
                    app_name=app_name,
                    window_title=window_title,
                    process_name=process_name,
                    ts_start=now,
                )
                self._last_app = app_name
                self._last_title = window_title
                self._last_heartbeat = now_mono
                return event

            if changed_title:
                previous_title = self._last_title
                self._last_title = window_title
                if app_name in self._browser_app_names:
                    return None

                last_emit_at = self._last_title_change_emit_at.get(app_name)
                if last_emit_at is not None and (now_mono - last_emit_at) < 3.0:
                    return None

                self._last_title_change_emit_at[app_name] = now_mono
                self._last_heartbeat = now_mono
                return normalize_window_event(
                    event_type=WINDOW_TITLE_CHANGED_EVENT,
                    app_name=app_name,
                    window_title=window_title,
                    process_name=process_name,
                    ts_start=now,
                    metadata={
                        "previous_window_title": previous_title,
                        "current_window_title": window_title,
                    },
                )

            if (now_mono - self._last_heartbeat) >= HEARTBEAT_INTERVAL:
                event = normalize_window_event(
                    event_type=WINDOW_HEARTBEAT_EVENT,
                    app_name=app_name,
                    window_title=window_title,
                    process_name=process_name,
                    ts_start=now,
                )
                self._last_heartbeat = now_mono
                return event
        except Exception as e:
            logger.error(f"WindowWatcher error: {e}")
        return None

    def _run(self):
        """
        Poll for frontmost app changes every 1 second.
        Emit window_focus on app switch, window_title_changed on same-app title changes,
        window_heartbeat every 30s.
        """
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(1)
                continue
            event = self.capture_once()
            if event is not None:
                self.emit(event)

            time.sleep(1)
