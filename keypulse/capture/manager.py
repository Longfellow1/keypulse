from __future__ import annotations
import json
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from keypulse.capture.aggregator import Aggregator
from keypulse.capture.base import BaseWatcher
from keypulse.capture.policy import PolicyEngine
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.config import Config
from keypulse.privacy.desensitizer import desensitize, truncate
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent, SearchDoc
from keypulse.store.repository import (
    insert_raw_event, insert_search_doc, seed_policies_from_config, set_state, apply_retention
)
from keypulse.utils.logging import get_logger

logger = get_logger("manager")


class CaptureManager:
    def __init__(self, config: Config):
        self.config = config
        self._queue: queue.Queue = queue.Queue()
        self._watchers: dict[str, BaseWatcher] = {}
        self._policy = PolicyEngine()
        self._aggregator = Aggregator()
        self._running = threading.Event()
        self._paused = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None

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
        logger.info("CaptureManager started")

    def stop(self):
        """Stop all watchers and flush remaining events."""
        self._running.clear()
        for w in self._watchers.values():
            w.stop()
        self._drain_queue()
        self._aggregator.flush()
        if self._flush_thread:
            self._flush_thread.join(timeout=10)
        set_state("status", "stopped")
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

    def _init_watchers(self):
        cfg = self.config.watchers
        if cfg.window:
            from keypulse.capture.watchers.window import WindowWatcher
            self._watchers["window"] = WindowWatcher(self._queue)
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

    def _flush_loop(self):
        while self._running.is_set():
            if not self._paused.is_set():
                self._drain_queue()
            time.sleep(self.config.app.flush_interval_sec)

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

    def _process_event(self, event: RawEvent):
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

        # 3. Session tracking
        session = self._aggregator.process(result)
        if session:
            result.session_id = session.id

        # 4. Persist raw event
        row_id = insert_raw_event(result)
        result.id = row_id

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
