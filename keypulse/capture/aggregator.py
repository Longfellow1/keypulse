from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional
from keypulse.store.models import RawEvent, Session
from keypulse.store.repository import upsert_session
from keypulse.store.db import get_conn


class Aggregator:
    """Tracks current session, cuts sessions on window-switch or idle."""

    def __init__(self):
        self._current_session: Optional[Session] = None

    def process(self, event: RawEvent) -> Optional[Session]:
        """
        Update session state for this event.
        Returns the current session (after update), or None.
        """
        now = event.ts_start

        # Idle start: close current session
        if event.event_type == "idle_start":
            if self._current_session:
                self._close_current(now)
            return None

        # Idle end / resume: will open new session on next window event
        if event.event_type == "idle_end":
            return None

        # Window focus: new app → new session
        if event.event_type == "window_focus":
            if self._current_session and self._current_session.app_name != event.app_name:
                self._close_current(now)
            if not self._current_session:
                self._current_session = Session(
                    started_at=now,
                    ended_at=now,
                    app_name=event.app_name,
                    primary_window_title=event.window_title,
                )

        # Update current session if exists
        if self._current_session:
            self._current_session.ended_at = now
            self._current_session.event_count += 1
            try:
                start = datetime.fromisoformat(self._current_session.started_at)
                end = datetime.fromisoformat(now)
                self._current_session.duration_sec = int((end - start).total_seconds())
            except Exception:
                pass
            # Update title if window_title changed
            if event.window_title and event.event_type == "window_focus":
                self._current_session.primary_window_title = event.window_title
            upsert_session(self._current_session)

        return self._current_session

    def _close_current(self, ended_at: str):
        if self._current_session:
            self._current_session.ended_at = ended_at
            try:
                start = datetime.fromisoformat(self._current_session.started_at)
                end = datetime.fromisoformat(ended_at)
                self._current_session.duration_sec = int((end - start).total_seconds())
            except Exception:
                pass
            upsert_session(self._current_session)
        self._current_session = None

    def flush(self):
        """Force-close current session with current time."""
        now = datetime.now(timezone.utc).isoformat()
        self._close_current(now)

    def current_session(self) -> Optional[Session]:
        return self._current_session
