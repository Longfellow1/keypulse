from __future__ import annotations
import json
from fnmatch import fnmatch
import os
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Literal, Optional

from keypulse.capture.aggregator import Aggregator
from keypulse.capture.base import BaseWatcher
from keypulse.capture.camera_monitor import CameraMonitor
from keypulse.capture.fusion import CaptureFusionEngine
from keypulse.capture.policy import PolicyEngine
from keypulse.capture.provider import build_ocr_provider
from keypulse.capture.normalizer import (
    WINDOW_FOCUS_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
    is_window_session_event_type,
    normalize_manual_event,
)
from keypulse.config import Config
from keypulse.privacy.desensitizer import desensitize, truncate
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent, SearchDoc
from keypulse.store.repository import (
    apply_retention,
    get_state,
    insert_raw_event,
    insert_search_doc,
    seed_policies_from_config,
    set_state,
)
from keypulse.utils.logging import get_logger

logger = get_logger("manager")
_USER_SOURCES = frozenset({"keyboard_chunk", "clipboard", "manual", "browser", "ax_text", "ax_ime_commit", "ax_snapshot_fallback"})


def _derive_speaker(event: RawEvent) -> Literal["user", "system"]:
    return "user" if event.source in _USER_SOURCES else "system"


class CaptureManager:
    def __init__(self, config: Config):
        self.config = config
        self._queue: queue.Queue = queue.Queue()
        self._watchers: dict[str, BaseWatcher] = {}
        self._policy = PolicyEngine()
        self._aggregator = Aggregator()
        self._fusion = CaptureFusionEngine()
        self._running = threading.Event()
        self._paused = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self._camera_monitor: Optional[CameraMonitor] = None
        self._runtime_state_last_persist = 0.0
        self._source_counts: dict[str, int] = {}
        self._source_last_event_at: dict[str, str] = {}

    def start(self):
        """Initialize DB, seed policies, start watchers and flush loop."""
        init_db(self.config.db_path_expanded)
        seed_policies_from_config(self.config.policies)
        self._policy.reload()
        apply_retention(self.config.app.retention_days)

        self._init_watchers()
        for w in self._watchers.values():
            w.start()

        self._running.set()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="flush-loop"
        )
        self._flush_thread.start()
        set_state("status", "running")
        set_state("started_at", datetime.now(timezone.utc).isoformat())
        self._persist_runtime_state(force=True)
        logger.info("CaptureManager started")
        self._start_camera_monitor()

    def stop(self):
        """Stop all watchers and flush remaining events."""
        if self._camera_monitor is not None:
            self._camera_monitor.stop()
            self._camera_monitor = None
        self._running.clear()
        for w in self._watchers.values():
            w.stop()
        self._drain_queue()
        self._flush_window_session(reason="watcher_stop")
        self._aggregator.flush()
        if self._flush_thread:
            self._flush_thread.join(timeout=10)
        set_state("status", "stopped")
        self._persist_runtime_state(force=True)
        logger.info("CaptureManager stopped")

    def pause(self):
        self._paused.set()
        for w in self._watchers.values():
            w.pause()
        set_state("status", "paused")

    def resume(self):
        self._paused.clear()
        for w in self._watchers.values():
            w.resume()
        set_state("status", "running")

    def pause_watchers(self, names: list[str]) -> None:
        for name in names:
            watcher = self._watchers.get(name)
            if watcher is not None:
                watcher.pause()

    def resume_watchers(self, names: list[str]) -> None:
        for name in names:
            watcher = self._watchers.get(name)
            if watcher is not None:
                watcher.resume()

    def health(self) -> dict:
        return {
            "running": self._running.is_set(),
            "paused": self._paused.is_set(),
            "watchers": {name: w.health() for name, w in self._watchers.items()},
            "queue_size": self._queue.qsize(),
        }

    def save_manual(self, text: str, tags: Optional[str] = None):
        """Inject a manual save event directly."""
        event = normalize_manual_event(text=text, tags=tags)
        self._queue.put(event)

    def record_keyboard_input(
        self,
        text: str,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        now: float | None = None,
    ) -> None:
        watcher = self._watchers.get("keyboard_chunk")
        if watcher is not None and hasattr(watcher, "record_input"):
            watcher.record_input(
                text=text,
                app_name=app_name,
                window_title=window_title,
                process_name=process_name,
                now=now,
            )
        ocr_watcher = self._watchers.get("ocr")
        if ocr_watcher is not None and hasattr(ocr_watcher, "note_keyboard_activity"):
            ocr_watcher.note_keyboard_activity(now=now)

    def capture_ocr_image(
        self,
        image_ref,
        now: float | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        ax_text_available: bool = False,
        content_signature: str | None = None,
    ):
        ocr_watcher = self._watchers.get("ocr")
        if ocr_watcher is None or not hasattr(ocr_watcher, "capture_once"):
            return None
        from keypulse.capture.watchers.ocr import OCRContext

        return ocr_watcher.capture_once(
            now=now,
            image_ref=image_ref,
            context=OCRContext(
                app_name=app_name,
                window_title=window_title,
                process_name=process_name,
                ax_text_available=ax_text_available,
                content_signature=content_signature,
            ),
        )

    def _init_watchers(self):
        cfg = self.config.watchers
        if cfg.window:
            from keypulse.capture.watchers.window import WindowWatcher
            self._watchers["window"] = WindowWatcher(
                self._queue,
                browser_app_names=self.config.browser.supported_browsers,
            )
        if cfg.idle:
            from keypulse.capture.watchers.idle import IdleWatcher
            self._watchers["idle"] = IdleWatcher(
                self._queue, self.config.idle.threshold_sec
            )
        if cfg.clipboard:
            from keypulse.capture.watchers.clipboard import ClipboardWatcher
            self._watchers["clipboard"] = ClipboardWatcher(
                self._queue,
                max_text_length=self.config.clipboard.max_text_length,
                dedup_window_sec=self.config.clipboard.dedup_window_sec,
            )
        if cfg.manual:
            from keypulse.capture.watchers.manual import ManualWatcher
            self._watchers["manual"] = ManualWatcher(self._queue)
        if cfg.ax_text:
            from keypulse.capture.watchers.ax_text import AXTextWatcher

            self._watchers["ax_text"] = AXTextWatcher(
                self._queue,
                poll_interval_sec=self.config.ax_text.poll_interval_sec,
            )
        if cfg.browser:
            from keypulse.capture.watchers.browser import BrowserWatcher

            self._watchers["browser"] = BrowserWatcher(
                self._queue,
                poll_interval_sec=self.config.browser.poll_interval_sec,
                supported_browsers=self.config.browser.supported_browsers,
            )
        if cfg.keyboard_chunk:
            from keypulse.capture.watchers.keyboard_chunk import KeyboardChunkWatcher

            self._watchers["keyboard_chunk"] = KeyboardChunkWatcher(
                self._queue,
                silence_sec=self.config.keyboard_chunk.silence_sec,
                force_flush_sec=self.config.keyboard_chunk.force_flush_sec,
            )
        if cfg.ocr:
            from keypulse.capture.watchers.ocr import OCRTriggerGate, OCRWatcher

            self._watchers["ocr"] = OCRWatcher(
                self._queue,
                provider=build_ocr_provider(self.config.ocr.provider),
                trigger_gate=OCRTriggerGate(
                    window_switch_delay_sec=self.config.ocr.window_switch_delay_sec,
                    stable_interval_sec=self.config.ocr.stable_interval_sec,
                    keyboard_quiet_sec=self.config.ocr.keyboard_quiet_sec,
                ),
            )

    def _start_camera_monitor(self) -> None:
        if not self.config.privacy.camera_scene_pause:
            return
        watched_names = [name for name in ("ax_text", "ocr") if name in self._watchers]
        if not watched_names:
            return

        def on_change(in_use: bool) -> None:
            if in_use:
                logger.info("Camera in use, pausing ax_text/ocr")
                self.pause_watchers(watched_names)
            else:
                logger.info("Camera released, resuming ax_text/ocr")
                self.resume_watchers(watched_names)

        self._camera_monitor = CameraMonitor(on_change=on_change)
        self._camera_monitor.start()

    def _flush_loop(self):
        while self._running.is_set():
            self._sync_control_state()
            if not self._paused.is_set():
                self._drain_queue()
            self._persist_runtime_state()
            time.sleep(self.config.app.flush_interval_sec)

    def _sync_control_state(self):
        desired = get_state("status") or "running"
        paused_until = get_state("paused_until") or ""
        if desired == "paused" and paused_until:
            try:
                if time.time() >= float(paused_until):
                    self.resume()
                    set_state("paused_until", "")
                    return
            except Exception:
                set_state("paused_until", "")
        if desired == "paused" and not self._paused.is_set():
            self.pause()
        elif desired == "running" and self._paused.is_set():
            self.resume()

    def _drain_queue(self):
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        for event in events:
            try:
                self._process_event(event)
            except Exception as e:
                logger.error(f"Error processing event: {e}")

    def _is_blacklisted(self, event: RawEvent) -> bool:
        candidate = event.process_name or event.app_name
        if not candidate:
            return False
        if candidate in self.config.privacy.blacklist_bundle_ids:
            return True
        return any(fnmatch(candidate, pattern) for pattern in self.config.privacy.blacklist_patterns)

    def _process_event(self, event: RawEvent):
        event.speaker = _derive_speaker(event)

        if self._is_blacklisted(event):
            logger.debug(
                f"Event blacklisted: {event.process_name or event.app_name} ({event.source}/{event.event_type})"
            )
            return

        self._sync_window_idle_state(event)
        self._feed_light_capture_watchers(event)
        if event.source == "window" and event.event_type in {WINDOW_FOCUS_EVENT, WINDOW_TITLE_CHANGED_EVENT}:
            return

        # 1. Policy
        result = self._policy.apply(event)
        if result is None:
            logger.debug(f"Event denied by policy: {event.source}/{event.event_type}")
            return

        # 2. Desensitize content
        if result.content_text:
            result.content_text = desensitize(
                result.content_text,
                redact_emails=self.config.privacy.redact_emails,
                redact_phones=self.config.privacy.redact_phones,
                redact_tokens=self.config.privacy.redact_tokens,
            )
            result.content_text = truncate(
                result.content_text, self.config.clipboard.max_text_length
            )
            fusion = self._fusion.fuse(result)
            if not fusion.persist:
                return
            if fusion.event is not None:
                result = fusion.event

        if result.window_title:
            result.window_title = desensitize(
                result.window_title,
                redact_emails=self.config.privacy.redact_emails,
                redact_phones=self.config.privacy.redact_phones,
                redact_tokens=self.config.privacy.redact_tokens,
            )

        if result.session_id is None and result.event_type not in {"idle_start", "idle_end"}:
            session_id = self._current_window_session_id()
            if session_id:
                result.session_id = session_id

        # 3. Session tracking
        session = self._aggregator.process(result)
        if session and result.session_id is None:
            result.session_id = session.id

        # 4. Persist raw event
        row_id = insert_raw_event(result)
        result.id = row_id
        self._record_runtime_event(result)

        # 5. Index searchable content
        if result.source in ("clipboard", "manual") and result.content_text:
            tags = None
            if result.metadata_json:
                try:
                    tags = json.loads(result.metadata_json).get("tags")
                except Exception:
                    pass
            doc = SearchDoc(
                ref_type=result.source,
                ref_id=str(row_id),
                title=result.window_title or result.app_name,
                body=result.content_text,
                tags=tags,
                app_name=result.app_name,
            )
            insert_search_doc(doc)

        set_state("last_flush", datetime.now(timezone.utc).isoformat())

    def _feed_light_capture_watchers(self, event: RawEvent) -> None:
        ocr_watcher = self._watchers.get("ocr")
        if ocr_watcher is None:
            return
        # window_focus and window_title_changed both mean the frontmost context moved.
        if event.source == "window" and is_window_session_event_type(event.event_type) and hasattr(ocr_watcher, "note_window_change"):
            ocr_watcher.note_window_change()
            return
        if event.source == "window" and event.event_type == "window_focus_session" and hasattr(ocr_watcher, "note_window_change"):
            try:
                metadata = json.loads(event.metadata_json or "{}")
            except Exception:
                metadata = {}
            if metadata.get("reason") == "app_switch":
                ocr_watcher.note_window_change()

    def _current_window_session_id(self) -> str | None:
        watcher = self._watchers.get("window")
        if watcher is None or not hasattr(watcher, "current_session_id"):
            return None
        session_id = watcher.current_session_id()
        return str(session_id) if session_id else None

    def _flush_window_session(self, reason: str) -> None:
        watcher = self._watchers.get("window")
        if watcher is None or not hasattr(watcher, "flush_current_session"):
            return
        event = watcher.flush_current_session(
            ended_at=datetime.now(timezone.utc).isoformat(),
            ended_at_mono=time.monotonic(),
            reason=reason,
        )
        if event is not None:
            self._process_event(event)

    def _sync_window_idle_state(self, event: RawEvent) -> None:
        watcher = self._watchers.get("window")
        if watcher is None:
            return
        if event.event_type == "idle_start":
            self._flush_window_session(reason="idle_timeout")
            if hasattr(watcher, "set_idle"):
                watcher.set_idle(True)
        elif event.event_type == "idle_end" and hasattr(watcher, "set_idle"):
            watcher.set_idle(False)

    def _record_runtime_event(self, event: RawEvent) -> None:
        self._source_counts[event.source] = self._source_counts.get(event.source, 0) + 1
        self._source_last_event_at[event.source] = event.ts_start or datetime.now(timezone.utc).isoformat()

    def _runtime_snapshot(self) -> dict:
        multi_source_counts = {
            source: self._source_counts.get(source, 0)
            for source in ("ax_text", "ocr_text", "keyboard_chunk", "clipboard", "manual")
        }
        last_seen = {
            source: self._source_last_event_at.get(source)
            for source in ("ax_text", "ocr_text", "keyboard_chunk", "clipboard", "manual")
            if self._source_last_event_at.get(source)
        }
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "host_executable": os.path.realpath(os.sys.executable),
            "running": self._running.is_set(),
            "paused": self._paused.is_set(),
            "watchers": {name: watcher.health() for name, watcher in self._watchers.items()},
            "queue_size": self._queue.qsize(),
            "multi_source_counts": multi_source_counts,
            "last_seen": last_seen,
        }

    def _persist_runtime_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._runtime_state_last_persist) < 2.0:
            return
        snapshot = self._runtime_snapshot()
        set_state("capture_runtime", json.dumps(snapshot, ensure_ascii=False))
        self._runtime_state_last_persist = now
