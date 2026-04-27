from __future__ import annotations

from keypulse.utils.text_filters import looks_like_ime_composition

import queue
import time
import unicodedata

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_keyboard_chunk_event
from keypulse.capture.watchers.keyboard_native import build_keyboard_input_source


def _normalize_buffered_text(parts: list[str]) -> str:
    chars: list[str] = []
    for part in parts:
        for ch in part:
            if ch in ("\b", "\x7f"):
                if chars:
                    chars.pop()
                continue
            if ch in ("\n", "\t"):
                chars.append(ch)
                continue
            if unicodedata.category(ch).startswith("C"):
                continue
            chars.append(ch)
    return "".join(chars).strip()


class KeyboardChunkBuffer:
    def __init__(self, silence_sec: float = 2.0, force_flush_sec: float = 2.0):
        self._silence_sec = silence_sec
        self._force_flush_sec = force_flush_sec
        self._parts: list[str] = []
        self._started_at: float | None = None
        self._last_input_at: float | None = None
        self._app_name: str | None = None
        self._window_title: str | None = None
        self._process_name: str | None = None

    def add_text(
        self,
        text: str,
        app_name: str | None,
        window_title: str | None,
        now: float,
        process_name: str | None = None,
    ) -> None:
        if self._started_at is None:
            self._started_at = now
        self._last_input_at = now
        self._app_name = app_name
        self._window_title = window_title
        self._process_name = process_name
        self._parts.append(text)

    def flush_if_due(self, now: float):
        if self._started_at is None or self._last_input_at is None:
            return None
        if (now - self._last_input_at) < self._silence_sec and (now - self._started_at) < self._force_flush_sec:
            return None
        return self._flush()

    def _flush(self):
        text = _normalize_buffered_text(self._parts)
        self._parts = []
        self._started_at = None
        self._last_input_at = None
        if not text:
            return None
        return normalize_keyboard_chunk_event(
            text=text,
            app_name=self._app_name,
            window_title=self._window_title,
            process_name=self._process_name,
            metadata={"chunk_mode": "buffered"},
        )


class KeyboardChunkWatcher(BaseWatcher):
    name = "keyboard_chunk"

    def __init__(
        self,
        event_queue: queue.Queue,
        silence_sec: float = 2.0,
        force_flush_sec: float = 2.0,
        source=None,
    ):
        super().__init__(event_queue)
        self._buffer = KeyboardChunkBuffer(
            silence_sec=silence_sec,
            force_flush_sec=force_flush_sec,
        )
        self._source = source if source is not None else build_keyboard_input_source()
        self._source_started = False

    def start(self):
        if self._source is not None and not self._source_started:
            try:
                self._source_started = bool(self._source.start(self.record_input))
            except Exception:
                self._source_started = False
        super().start()

    def stop(self):
        if self._source is not None and self._source_started:
            try:
                self._source.stop()
            except Exception:
                pass
            self._source_started = False
        super().stop()

    def health(self) -> dict:
        status = super().health()
        if self._source is not None and hasattr(self._source, "health"):
            try:
                status["source"] = self._source.health()
            except Exception:
                status["source"] = {"status": "error"}
        return status

    def record_input(
        self,
        text: str,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        now: float | None = None,
        secure_input: bool = False,
        denied: bool = False,
    ) -> None:
        if secure_input or denied:
            return
        # Early filter: drop IME composition blobs at capture source
        if looks_like_ime_composition(text):
            return
        self._buffer.add_text(
            text=text,
            app_name=app_name,
            window_title=window_title,
            process_name=process_name,
            now=time.monotonic() if now is None else now,
        )

    def _run(self):
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(0.25)
                continue
            event = self._buffer.flush_if_due(now=time.monotonic())
            if event is not None:
                self.emit(event)
            time.sleep(0.25)
