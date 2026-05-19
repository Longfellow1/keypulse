from __future__ import annotations

import queue
import time
from typing import Callable

from keypulse.capture.active_app import SYSTEM_OVERLAY_APPS
from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_ax_text_event


AXTextPayload = dict[str, str | None]


def _empty_payload(
    app_name: str | None = None,
    window_title: str | None = None,
    process_name: str | None = None,
) -> AXTextPayload:
    return {
        "text": "",
        "selected_text": None,
        "value_text": None,
        "title_text": None,
        "app_name": app_name,
        "window_title": window_title,
        "process_name": process_name,
    }


def _coerce_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    string_value = getattr(value, "stringValue", None)
    if callable(string_value):
        try:
            return string_value()
        except Exception:
            return None
    description = getattr(value, "description", None)
    if callable(description):
        try:
            return description()
        except Exception:
            return None
    return str(value)


def _read_ax_attribute(application_services_module, element, attribute: str) -> str | None:
    try:
        error, value = application_services_module.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    if error != getattr(application_services_module, "kAXErrorSuccess", 0):
        return None
    text = _coerce_text(value)
    if text is None:
        return None
    return text.strip()


def _read_ax_element(application_services_module, element, attribute: str):
    try:
        error, value = application_services_module.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    if error != getattr(application_services_module, "kAXErrorSuccess", 0):
        return None
    return value


def read_frontmost_ax_text(
    appkit_module=None,
    application_services_module=None,
) -> AXTextPayload | None:
    try:
        appkit = appkit_module
        if appkit is None:
            import AppKit as appkit

        application_services = application_services_module
        if application_services is None:
            import ApplicationServices as application_services
    except Exception:
        return _empty_payload()

    try:
        app = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return _empty_payload()

        app_name = app.localizedName()
        if app_name in SYSTEM_OVERLAY_APPS:
            return _empty_payload()

        process_name = app.bundleIdentifier() or app_name
        pid = app.processIdentifier()
        app_element = application_services.AXUIElementCreateApplication(pid)
        focused_element = _read_ax_element(application_services, app_element, "AXFocusedUIElement")
        focused_window = _read_ax_element(application_services, app_element, "AXFocusedWindow")
    except Exception:
        return _empty_payload()

    selected_text = None
    value_text = None
    title_text = None
    window_title = None

    if focused_element:
        selected_text = _read_ax_attribute(application_services, focused_element, "AXSelectedText")
        value_text = _read_ax_attribute(application_services, focused_element, "AXValue")
        title_text = _read_ax_attribute(application_services, focused_element, "AXTitle")
    if focused_window:
        window_title = _read_ax_attribute(application_services, focused_window, "AXTitle")

    return {
        "text": selected_text or value_text or title_text or "",
        "selected_text": selected_text,
        "value_text": value_text,
        "title_text": title_text,
        "app_name": app_name,
        "window_title": window_title,
        "process_name": process_name,
    }


class AXTextWatcher(BaseWatcher):
    name = "ax_text"
    MIN_POLL_INTERVAL_SEC = 1.0
    MAX_READS_PER_SEC = 1.0

    # Polls the foreground app every 1s. If macOS revokes Accessibility
    # permission silently, no exception is raised — the watcher keeps
    # looping but never emits. 30 minutes is generous enough to avoid
    # false-positive revivals while a user reads a static page.
    HEARTBEAT_TIMEOUT_SEC = 1800.0

    def __init__(
        self,
        event_queue: queue.Queue,
        poll_interval_sec: float = 1.0,
        min_poll_interval_sec: float = MIN_POLL_INTERVAL_SEC,
        max_reads_per_sec: float = MAX_READS_PER_SEC,
        text_reader: Callable[[], AXTextPayload | None] | None = None,
    ):
        super().__init__(event_queue)
        self._poll_interval = poll_interval_sec
        self._min_poll_interval = max(0.01, float(min_poll_interval_sec))
        self._max_reads_per_sec = max(0.1, float(max_reads_per_sec))
        self._text_reader = text_reader or read_frontmost_ax_text
        self._last_hash: str | None = None

    def capture_once(self):
        payload = self._text_reader()
        if not payload:
            return None
        text = (payload.get("text") or "").strip()
        if not text:
            return None
        event = normalize_ax_text_event(
            text=text,
            app_name=payload.get("app_name"),
            window_title=payload.get("window_title"),
            process_name=payload.get("process_name"),
        )
        if event.content_hash == self._last_hash:
            return None
        self._last_hash = event.content_hash
        return event

    def _run(self):
        while self._running.is_set():
            cycle_started_at = time.monotonic()
            if self._paused.is_set():
                self.beat()
            else:
                event = self.capture_once()
                if event is not None:
                    self.emit(event)
                else:
                    # Same-content polls dedupe to None — beat so heartbeat
                    # supervision can tell "alive but no new text" from "stuck".
                    self.beat()

            max_rate_interval = 1.0 / self._max_reads_per_sec
            sleep_interval = max(self._poll_interval, self._min_poll_interval, max_rate_interval)
            elapsed = time.monotonic() - cycle_started_at
            remaining = sleep_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
