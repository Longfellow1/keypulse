from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Callable

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_ocr_text_event
from keypulse.capture.provider import OCRRequest, build_ocr_provider
from keypulse.capture.watchers.ax_text import read_frontmost_ax_text
from keypulse.capture.watchers.window import _get_frontmost_app
from keypulse.capture.watchers.window_capture import capture_frontmost_window_image


@dataclass(frozen=True)
class OCRContext:
    app_name: str | None = None
    window_title: str | None = None
    process_name: str | None = None
    image_ref: object | None = None
    ax_text_available: bool = False
    denied: bool = False
    secure_input: bool = False
    content_signature: str | None = None


class OCRTriggerGate:
    def __init__(
        self,
        window_switch_delay_sec: float = 0.8,
        stable_interval_sec: float = 10.0,
        keyboard_quiet_sec: float = 2.0,
    ):
        self._window_switch_delay_sec = window_switch_delay_sec
        self._stable_interval_sec = stable_interval_sec
        self._keyboard_quiet_sec = keyboard_quiet_sec
        self._last_window_change_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_keyboard_activity_at: float | None = None
        self._last_signature: str | None = None

    def note_window_change(self, now: float) -> None:
        self._last_window_change_at = now

    def note_keyboard_activity(self, now: float) -> None:
        self._last_keyboard_activity_at = now

    def should_trigger(
        self,
        now: float,
        ax_text_available: bool,
        content_signature: str | None,
        denied: bool = False,
        secure_input: bool = False,
    ) -> str | None:
        if denied or secure_input:
            return None
        keyboard_after_window_change = (
            self._last_keyboard_activity_at is not None
            and (
                self._last_window_change_at is None
                or self._last_keyboard_activity_at >= self._last_window_change_at
            )
        )
        waiting_for_keyboard_quiet = (
            not ax_text_available
            and keyboard_after_window_change
            and now - self._last_keyboard_activity_at < self._keyboard_quiet_sec
        )
        if (
            not ax_text_available
            and keyboard_after_window_change
            and not waiting_for_keyboard_quiet
            and (self._last_attempt_at is None or self._last_keyboard_activity_at > self._last_attempt_at)
        ):
            self._last_attempt_at = now
            self._last_signature = content_signature
            return "keyboard_quiet"
        if (
            not ax_text_available
            and self._last_window_change_at is not None
            and now - self._last_window_change_at >= self._window_switch_delay_sec
        ):
            if not keyboard_after_window_change and (
                self._last_attempt_at is None or self._last_window_change_at > self._last_attempt_at
            ):
                self._last_attempt_at = now
                self._last_signature = content_signature
                return "window_switch"
        if (
            content_signature
            and content_signature != self._last_signature
            and not ax_text_available
            and not waiting_for_keyboard_quiet
            and self._last_attempt_at is not None
            and now - self._last_attempt_at >= self._stable_interval_sec
        ):
            self._last_attempt_at = now
            self._last_signature = content_signature
            return "window_stable_changed"
        return None


class OCRWatcher(BaseWatcher):
    name = "ocr"

    def __init__(
        self,
        event_queue: queue.Queue | None,
        provider=None,
        trigger_gate: OCRTriggerGate | None = None,
        provider_name: str = "vision_native",
        context_reader: Callable[[float], OCRContext] | None = None,
        image_provider: Callable[[OCRContext], object | None] | None = None,
    ):
        super().__init__(event_queue or queue.Queue())
        self._provider = provider or build_ocr_provider(provider_name)
        self._trigger_gate = trigger_gate or OCRTriggerGate()
        self._context_reader = context_reader or self._read_frontmost_context
        self._image_provider = image_provider or (lambda _context: capture_frontmost_window_image())

    def note_window_change(self, now: float | None = None) -> None:
        self._trigger_gate.note_window_change(time.monotonic() if now is None else now)

    def note_keyboard_activity(self, now: float | None = None) -> None:
        self._trigger_gate.note_keyboard_activity(time.monotonic() if now is None else now)

    def capture_once(
        self,
        now: float | None = None,
        context: OCRContext | None = None,
        image_ref: object | None = None,
    ):
        if not self._provider.is_available():
            return None

        attempt_now = time.monotonic() if now is None else now
        if context is not None:
            capture_context = context
        elif image_ref is not None:
            capture_context = OCRContext()
        else:
            capture_context = self._context_reader(attempt_now)
        reason = self._trigger_gate.should_trigger(
            now=attempt_now,
            ax_text_available=capture_context.ax_text_available,
            content_signature=capture_context.content_signature,
            denied=capture_context.denied,
            secure_input=capture_context.secure_input,
        )
        if reason is None:
            return None
        resolved_image_ref = image_ref if image_ref is not None else capture_context.image_ref
        if resolved_image_ref is None:
            resolved_image_ref = self._image_provider(capture_context)
        if resolved_image_ref is None:
            return None

        result = self._provider.recognize(
            OCRRequest(
                app_name=capture_context.app_name,
                window_title=capture_context.window_title,
                process_name=capture_context.process_name,
                image_ref=resolved_image_ref,
                metadata={"trigger_reason": reason},
            )
        )
        if not result.ok or not result.text:
            return None

        return normalize_ocr_text_event(
            text=result.text,
            app_name=capture_context.app_name,
            window_title=capture_context.window_title,
            process_name=capture_context.process_name,
            metadata={
                "provider": result.provider,
                "trigger_reason": reason,
                **result.metadata,
            },
        )

    def _read_frontmost_context(self, _now: float) -> OCRContext:
        ax_payload = read_frontmost_ax_text() or {}
        app_name = ax_payload.get("app_name")
        window_title = ax_payload.get("window_title")
        process_name = ax_payload.get("process_name")
        ax_text = (ax_payload.get("text") or "").strip()

        if not app_name:
            app_name, window_title, process_name = _get_frontmost_app()

        signature = ax_text or "|".join(part for part in (app_name, window_title, process_name) if part) or None
        return OCRContext(
            app_name=app_name,
            window_title=window_title,
            process_name=process_name,
            ax_text_available=bool(ax_text),
            content_signature=signature,
        )

    def _run(self):
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(0.5)
                continue
            event = self.capture_once()
            if event is not None:
                self.emit(event)
            time.sleep(0.5)
