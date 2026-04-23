from __future__ import annotations
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import (
    WINDOW_FOCUS_EVENT,
    WINDOW_FOCUS_SESSION_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
    normalize_window_event,
)
from keypulse.capture.watchers.browser import DEFAULT_SUPPORTED_BROWSERS
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.window")

TITLE_STABLE_FLUSH_INTERVAL = 180  # seconds


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
        self._last_title_change_emit_at: dict[str, float] = {}
        self._browser_app_names = tuple(browser_app_names or DEFAULT_SUPPORTED_BROWSERS)
        self._current_session_id: Optional[str] = None
        self._session_started_at: Optional[str] = None
        self._session_started_mono: Optional[float] = None
        self._current_app: Optional[str] = None
        self._current_title: Optional[str] = None
        self._current_process_name: Optional[str] = None
        self._title_started_mono: Optional[float] = None
        self._title_durations: dict[str, float] = {}
        self._idle = False

    def current_session_id(self) -> Optional[str]:
        return self._current_session_id

    def set_idle(self, idle: bool) -> None:
        self._idle = idle

    def _start_session(
        self,
        app_name: str,
        window_title: Optional[str],
        process_name: Optional[str],
        *,
        started_at: str,
        started_mono: float,
    ) -> None:
        self._current_session_id = str(uuid.uuid4())
        self._session_started_at = started_at
        self._session_started_mono = started_mono
        self._current_app = app_name
        self._current_title = window_title
        self._current_process_name = process_name
        self._title_started_mono = started_mono
        self._title_durations = {}

    def _clear_session(self) -> None:
        self._current_session_id = None
        self._session_started_at = None
        self._session_started_mono = None
        self._current_app = None
        self._current_title = None
        self._current_process_name = None
        self._title_started_mono = None
        self._title_durations = {}

    def _record_title_span(self, ended_at_mono: float) -> None:
        if self._current_title is None or self._title_started_mono is None:
            return
        elapsed = max(ended_at_mono - self._title_started_mono, 0.0)
        self._title_durations[self._current_title] = self._title_durations.get(self._current_title, 0.0) + elapsed
        self._title_started_mono = ended_at_mono

    def _primary_title(self, ended_at_mono: float) -> Optional[str]:
        self._record_title_span(ended_at_mono)
        if not self._title_durations:
            return self._current_title
        return max(self._title_durations.items(), key=lambda item: item[1])[0]

    def flush_current_session(
        self,
        *,
        ended_at: str,
        ended_at_mono: float,
        reason: str,
    ):
        if (
            self._current_session_id is None
            or self._session_started_at is None
            or self._session_started_mono is None
            or self._current_app is None
        ):
            return None

        primary_title = self._primary_title(ended_at_mono)
        duration_sec = max(int(ended_at_mono - self._session_started_mono), 0)
        event = normalize_window_event(
            event_type=WINDOW_FOCUS_SESSION_EVENT,
            app_name=self._current_app,
            window_title=primary_title,
            process_name=self._current_process_name,
            ts_start=self._session_started_at,
            ts_end=ended_at,
            metadata={
                "duration_sec": duration_sec,
                "reason": reason,
                "active_app": self._current_app,
                "primary_title": primary_title,
            },
        )
        event.session_id = self._current_session_id
        self._clear_session()
        return event

    def capture_once(self):
        try:
            if self._idle:
                return None

            app_name, window_title, process_name = _get_frontmost_app()
            if not app_name:
                return None

            now = datetime.now(timezone.utc).isoformat()
            now_mono = time.monotonic()
            if self._current_session_id is None:
                self._start_session(
                    app_name,
                    window_title,
                    process_name,
                    started_at=now,
                    started_mono=now_mono,
                )
                return normalize_window_event(
                    event_type=WINDOW_FOCUS_EVENT,
                    app_name=app_name,
                    window_title=window_title,
                    process_name=process_name,
                    ts_start=now,
                )

            changed_app = app_name != self._current_app
            changed_title = window_title != self._current_title

            if changed_app:
                flushed = self.flush_current_session(
                    ended_at=now,
                    ended_at_mono=now_mono,
                    reason="app_switch",
                )
                self._start_session(
                    app_name,
                    window_title,
                    process_name,
                    started_at=now,
                    started_mono=now_mono,
                )
                return flushed

            if changed_title:
                previous_title = self._current_title
                self._record_title_span(now_mono)
                self._current_title = window_title
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

            if self._title_started_mono is not None and (now_mono - self._title_started_mono) >= TITLE_STABLE_FLUSH_INTERVAL:
                flushed = self.flush_current_session(
                    ended_at=now,
                    ended_at_mono=now_mono,
                    reason="title_stable",
                )
                self._start_session(
                    app_name,
                    window_title,
                    process_name,
                    started_at=now,
                    started_mono=now_mono,
                )
                return flushed
        except Exception as e:
            logger.error(f"WindowWatcher error: {e}")
        return None

    def _run(self):
        """
        Poll for frontmost app changes every 1 second.
        Emit window_focus on initial focus acquisition, window_title_changed on same-app
        title changes, and window_focus_session when a session boundary is flushed.
        """
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(1)
                continue
            event = self.capture_once()
            if event is not None:
                self.emit(event)

            time.sleep(1)
