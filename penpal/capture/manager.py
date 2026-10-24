from __future__ import annotations
import json
from fnmatch import fnmatch
import os
import queue
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Literal, Optional
from urllib.parse import urlsplit

from penpal.capture.aggregator import Aggregator
from penpal.capture.base import BaseWatcher
from penpal.capture.chat_apps import chat_app_key
from penpal.capture.camera_monitor import CameraMonitor
from penpal.capture.capture_control import (
    disabled_sources,
    is_capture_paused,
    source_writes_allowed,
)
from penpal.capture.fusion import CaptureFusionEngine
from penpal.capture.hygiene import (
    METADATA_ONLY,
    PAGE_SCOPED_STREAMS,
    capture_gate,
    is_blacklisted_app,
    is_system_overlay,
    is_terminal_app,
    host_matches_denylist as _host_matches_denylist,
    strip_url_query,
)
from penpal.capture.policy import PolicyEngine
# from penpal.capture.provider import build_ocr_provider
from penpal.capture.normalizer import (
    WINDOW_FOCUS_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
    is_window_session_event_type,
    normalize_manual_event,
)
from penpal.capture.user_presence import is_user_present
from penpal.capture.wake import LAST_WAKE_STATE_KEY, detect_wake
from penpal.config import Config
from penpal.privacy.desensitizer import desensitize, desensitize_json_value, truncate
from penpal.store.db import get_conn, init_db
from penpal.store.models import RawEvent, SearchDoc
from penpal.store.repository import (
    get_state,
    insert_raw_event,
    insert_search_doc,
    seed_policies_from_config,
    set_state,
)
from penpal.utils.logging import get_logger

logger = get_logger("manager")
_USER_SOURCES = frozenset(
    {
        "clipboard",
        "manual",
        "browser",
        "browser_url",
        "browser_history",
        "ax_text",
        "keyboard_chunk",
    }
)
_CHAT_STYLE_APPS = frozenset({"claude", "codex", "cursor", "obsidian", "notion", "visual studio code"})


def _derive_speaker(event: RawEvent) -> Literal["user", "system"]:
    return "user" if event.source in _USER_SOURCES else "system"


def _is_terminal_app(app_name: str | None) -> bool:
    return is_terminal_app(app_name)


def _metadata_payload(metadata_json: str | None) -> object | None:
    if not metadata_json:
        return None
    try:
        return json.loads(metadata_json)
    except Exception:
        return None


def _sanitize_metadata_json(
    metadata_json: str | None,
    *,
    extra_fields: dict[str, object] | None = None,
    redact_emails: bool,
    redact_phones: bool,
    redact_tokens: bool,
) -> str | None:
    if not metadata_json and not extra_fields:
        return metadata_json

    parsed = _metadata_payload(metadata_json)
    if parsed is None:
        if not extra_fields:
            return metadata_json
        parsed = {}

    sanitized = desensitize_json_value(
        parsed,
        redact_emails=redact_emails,
        redact_phones=redact_phones,
        redact_tokens=redact_tokens,
    )
    if extra_fields:
        if isinstance(sanitized, dict):
            sanitized = {**sanitized, **extra_fields}
        else:
            sanitized = dict(extra_fields)

    try:
        return json.dumps(sanitized, ensure_ascii=False)
    except Exception:
        return metadata_json if not extra_fields else json.dumps(extra_fields, ensure_ascii=False)


def _sanitize_content_text(
    text: str | None,
    *,
    redact_emails: bool,
    redact_phones: bool,
    redact_tokens: bool,
) -> str | None:
    if not text:
        return text
    return desensitize(
        text,
        redact_emails=redact_emails,
        redact_phones=redact_phones,
        redact_tokens=redact_tokens,
    )


def _browser_host_from_metadata(metadata_json: str | None) -> str | None:
    payload = _metadata_payload(metadata_json)
    if not isinstance(payload, dict):
        return None
    url = payload.get("url")
    if not url:
        return None
    try:
        host = urlsplit(str(url)).hostname
    except Exception:
        return None
    if not host:
        return None
    return host.lower()


def _should_drop_browser_event(event: RawEvent, deny_hosts: list[str]) -> bool:
    if event.source not in ("browser", "browser_url"):
        return False
    host = _browser_host_from_metadata(event.metadata_json)
    if not host:
        return False
    return _host_matches_denylist(host, deny_hosts)


def _event_url(event: RawEvent) -> str | None:
    payload = _metadata_payload(event.metadata_json)
    url = payload.get("url") if isinstance(payload, dict) else None
    return str(url) if url else None


def _strip_metadata_urls(metadata_json: str | None) -> str | None:
    payload = _metadata_payload(metadata_json)
    if not isinstance(payload, dict):
        return metadata_json
    if payload.get("url"):
        payload["url"] = strip_url_query(str(payload["url"]))
    entities = payload.get("entities")
    if isinstance(entities, dict) and isinstance(entities.get("urls"), list):
        entities["urls"] = [strip_url_query(str(u)) for u in entities["urls"]]
    return json.dumps(payload, ensure_ascii=False)


def _is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database or disk is full" in str(exc)


class CaptureManager:
    # While the disk is full, capture writes nothing and attempts one probe write per interval.
    DISK_FULL_PROBE_SEC: float = 60.0

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
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._camera_monitor: Optional[CameraMonitor] = None
        self._runtime_state_last_persist = 0.0
        self._source_counts: dict[str, int] = {}
        self._source_last_event_at: dict[str, str] = {}
        # D27: gate hits by "source:reason" (counts only, never content)
        self._gate_hits: dict[str, int] = {}
        # #26 / D27: last URL each browser app showed (app name -> URL), fed by the browser URL and tab
        # watchers. Keyboard, AX and clipboard text from that browser is checked against url_deny_hosts.
        self._browser_pages: dict[str, str] = {}
        # Disabled sources whose watchers we have already paused (#28).
        self._disabled_applied: frozenset[str] = frozenset()
        # Storage state: None = writing normally; "disk_full" = writes paused until the next probe.
        self._storage_error: Optional[str] = None
        self._storage_error_since: Optional[str] = None
        self._storage_dropped = 0
        self._storage_next_probe_mono = 0.0
        self._runtime_persist_failed = False
        self._wake_handler: Optional[Callable[[], None]] = None

    def start(self):
        """Initialize DB, seed policies, start watchers and flush loop."""
        init_db(self.config.db_path_expanded)
        seed_policies_from_config(self.config.policies)
        self._policy.reload()
        # D32: startup never deletes user data. Retention was removed; history is kept until an explicit purge.

        self._init_watchers()
        for w in self._watchers.values():
            w.start()

        self._running.set()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="flush-loop"
        )
        self._flush_thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="heartbeat-loop"
        )
        self._heartbeat_thread.start()
        # A persisted pause (penpal pause, HUD) survives restarts; only a non-paused daemon sets running.
        if not is_capture_paused():
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
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        if not is_capture_paused():  # keep a persisted pause across daemon restarts
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
        disabled = disabled_sources()
        for name, w in self._watchers.items():
            if name not in disabled:  # a switched-off source stays quiet
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
            "gate_hits": dict(self._gate_hits),
        }

    def save_manual(self, text: str, tags: Optional[str] = None):
        """Inject a manual save event directly."""
        event = normalize_manual_event(text=text, tags=tags)
        self._queue.put(event)

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
        # === OCR watcher 已下线 2026-05-14 ===
        # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
        # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
        # 历史 raw_events 中 ocr_text_capture 数据保留可读
        return None
        # ocr_watcher = self._watchers.get("ocr")
        # if ocr_watcher is None or not hasattr(ocr_watcher, "capture_once"):
        #     return None
        # from penpal.capture.watchers.ocr import OCRContext
        #
        # return ocr_watcher.capture_once(
        #     now=now,
        #     image_ref=image_ref,
        #     context=OCRContext(
        #         app_name=app_name,
        #         window_title=window_title,
        #         process_name=process_name,
        #         ax_text_available=ax_text_available,
        #         content_signature=content_signature,
        #     ),
        # )

    def _init_watchers(self):
        cfg = self.config.watchers
        if cfg.window:
            from penpal.capture.watchers.window import WindowWatcher
            self._watchers["window"] = WindowWatcher(
                self._queue,
                browser_app_names=self.config.browser.supported_browsers,
            )
        if cfg.idle:
            from penpal.capture.watchers.idle import IdleWatcher
            self._watchers["idle"] = IdleWatcher(
                self._queue, self.config.idle.threshold_sec
            )
        if cfg.clipboard:
            from penpal.capture.watchers.clipboard import ClipboardWatcher
            self._watchers["clipboard"] = ClipboardWatcher(
                self._queue,
                max_text_length=self.config.clipboard.max_text_length,
                dedup_window_sec=self.config.clipboard.dedup_window_sec,
            )
        if cfg.manual:
            from penpal.capture.watchers.manual import ManualWatcher
            self._watchers["manual"] = ManualWatcher(self._queue)
        if cfg.keyboard_chunk:
            from penpal.capture.watchers.keyboard_chunk import KeyboardChunkWatcher

            keyboard_cfg = getattr(self.config, "keyboard_chunk", None)
            self._watchers["keyboard_chunk"] = KeyboardChunkWatcher(
                self._queue,
                silence_sec=getattr(keyboard_cfg, "silence_sec", 2.0),
                force_flush_sec=getattr(keyboard_cfg, "force_flush_sec", 20.0),
                store_text=getattr(keyboard_cfg, "store_text", True),
            )
        if cfg.ax_text:
            from penpal.capture.watchers.ax_text import AXTextWatcher

            self._watchers["ax_text"] = AXTextWatcher(
                self._queue,
                poll_interval_sec=self.config.ax_text.poll_interval_sec,
            )
        if cfg.browser:
            from penpal.capture.watchers.browser import BrowserWatcher

            self._watchers["browser"] = BrowserWatcher(
                self._queue,
                poll_interval_sec=self.config.browser.poll_interval_sec,
                supported_browsers=self.config.browser.supported_browsers,
            )
        if cfg.browser_url:
            from penpal.capture.watchers.browser_url import BrowserUrlWatcher

            self._watchers["browser_url"] = BrowserUrlWatcher(
                self._queue,
                poll_interval_sec=self.config.browser_url.poll_interval_sec,
                supported_browsers=self.config.browser_url.supported_browsers,
                emit_on_url_change_only=self.config.browser_url.emit_on_url_change_only,
            )
        if self.config.browser_history.enabled:
            from penpal.capture.watchers.browser_history import BrowserHistoryWatcher

            self._watchers["browser_history"] = BrowserHistoryWatcher(
                self._queue,
                poll_interval_sec=self.config.browser_history.poll_interval_sec,
                enabled=self.config.browser_history.enabled,
                copy_to_cache=self.config.browser_history.copy_to_cache,
                browsers=self.config.browser_history.browsers,
            )
        # === OCR watcher 已下线 2026-05-14 ===
        # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
        # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
        # 历史 raw_events 中 ocr_text_capture 数据保留可读
        # if cfg.ocr:
        #     from penpal.capture.watchers.ocr import OCRTriggerGate, OCRWatcher
        #
        #     self._watchers["ocr"] = OCRWatcher(
        #         self._queue,
        #         provider=build_ocr_provider(self.config.ocr.provider),
        #         trigger_gate=OCRTriggerGate(
        #             window_switch_delay_sec=self.config.ocr.window_switch_delay_sec,
        #             stable_interval_sec=self.config.ocr.stable_interval_sec,
        #             keyboard_quiet_sec=self.config.ocr.keyboard_quiet_sec,
        #         ),
        #     )

    def _start_camera_monitor(self) -> None:
        if not self.config.privacy.camera_scene_pause:
            return
        # === OCR watcher 已下线 2026-05-14 ===
        # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
        # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
        # 历史 raw_events 中 ocr_text_capture 数据保留可读
        watched_names = [name for name in ("ax_text",) if name in self._watchers]
        if not watched_names:
            return

        def on_change(in_use: bool) -> None:
            if in_use:
                logger.info("Camera in use, pausing ax_text")
                self.pause_watchers(watched_names)
            else:
                logger.info("Camera released, resuming ax_text")
                self.resume_watchers(watched_names)

        self._camera_monitor = CameraMonitor(on_change=on_change)
        self._camera_monitor.start()

    HEARTBEAT_CHECK_INTERVAL_SEC: float = 60.0

    def _heartbeat_loop(self) -> None:
        """Detect and revive watchers that have gone silently mute.

        BaseWatcher.is_heartbeat_dead returns True when a watcher with a
        configured HEARTBEAT_TIMEOUT_SEC hasn't emitted within the timeout
        — typically because macOS revoked permission without raising. We
        stop+start the watcher (counts toward MAX_HEARTBEAT_REVIVALS) and
        log an ERROR so the user can see permission rotation events.
        """
        # Sleep first so quick pytest scenarios don't race startup.
        self._wait_for_next_check()
        while self._running.is_set():
            try:
                if not self._paused.is_set():
                    for name, watcher in list(self._watchers.items()):
                        try:
                            if watcher.is_heartbeat_dead():
                                watcher.heartbeat_revive()
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.error("heartbeat check for %s failed: %s", name, exc)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("heartbeat loop error: %s", exc)
            self._wait_for_next_check()

    def _wait_for_next_check(self) -> None:
        """Sleep one heartbeat interval in 0.5 s slices and watch the wall clock for a sleep/wake jump.

        A slice that took far longer on the wall clock than the 0.5 s it asked for means the Mac slept
        in between; the wake is handled at the first slice after resume (within ~0.5 s).
        """
        slept = 0.0
        while self._running.is_set() and slept < self.HEARTBEAT_CHECK_INTERVAL_SEC:
            wall_before = time.time()
            time.sleep(0.5)
            slept += 0.5
            if detect_wake(time.time() - wall_before, 0.5):
                self.handle_wake()

    def set_wake_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """Callback run after each detected wake, after the manager's own watcher restart.

        penpal.app wires it to the same capability self-check the supervisor runs, plus a source rescan.
        """
        self._wake_handler = handler

    def handle_wake(self) -> None:
        """Re-check the daemon after the Mac wakes from sleep.

        Also the test seam: a test injects a fake wake by calling this directly.
        Steps: record the wake time (the MCP grace window reads it), restart watchers that stopped, persist
        the runtime heartbeat now, then run the wake handler (permission self-check and source rescan).
        """
        logger.info("Mac woke from sleep: re-checking watchers, permissions and sources")
        try:
            set_state(LAST_WAKE_STATE_KEY, datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.error("recording wake time failed: %s", exc)
        self._restart_watchers_after_wake()
        try:
            self._persist_runtime_state(force=True)
        except Exception as exc:  # e.g. disk full: the flush loop reports it
            logger.error("runtime state not persisted after wake: %s", type(exc).__name__)
        handler = self._wake_handler
        if handler is not None:
            try:
                handler()
            except Exception as exc:
                logger.error("wake handler failed: %s", exc)

    def _restart_watchers_after_wake(self) -> None:
        """Restart enabled, non-paused watchers whose thread is gone (sleep can leave them silent).

        Watchers paused on purpose (camera, a source switched off, a user pause) are left alone, and so are
        live threads: a running but silent watcher is still covered by the heartbeat supervisor.
        """
        if self._paused.is_set():
            return
        disabled = disabled_sources()
        for name, watcher in list(self._watchers.items()):
            if name in disabled or watcher.is_paused() or watcher.is_running():
                continue
            try:
                watcher.start()
                logger.info("watcher %s restarted after wake", name)
            except Exception as exc:
                logger.error("watcher %s restart after wake failed: %s", name, exc)

    def _flush_loop(self):
        while self._running.is_set():
            self._sync_control_state()
            self._sync_source_switches()
            # Always drain: while paused or source-disabled, _process_event drops events at write time,
            # so nothing queued during a pause is replayed after resume.
            self._drain_queue()
            try:
                self._persist_runtime_state()
            except Exception as exc:  # e.g. disk full: keep the flush loop alive, report once
                if not self._runtime_persist_failed:
                    logger.error("capture runtime state not persisted: %s", type(exc).__name__)
                self._runtime_persist_failed = True
            else:
                self._runtime_persist_failed = False
            time.sleep(self.config.app.flush_interval_sec)

    def _sync_source_switches(self) -> None:
        """Quiet watchers of disabled sources (keyboard listener included). The write gate still applies."""
        disabled = disabled_sources()
        for name in disabled:
            watcher = self._watchers.get(name)
            if watcher is not None:
                watcher.pause()  # idempotent; re-applied every sync in case a watcher was restarted
        if not self._paused.is_set():
            for name in self._disabled_applied - disabled:
                watcher = self._watchers.get(name)
                if watcher is not None:
                    watcher.resume()
        self._disabled_applied = disabled

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
        privacy = self.config.privacy
        chat = chat_app_key(event.app_name, event.process_name)
        if chat is not None:
            # Chat apps are excluded by default (D19). An explicit opt-in overrides the default blacklist
            # entries for that app (bundle ids, com.tencent.* pattern, app names).
            return chat not in privacy.chat_apps_opt_in
        return is_blacklisted_app(
            event.app_name,
            event.process_name,
            bundle_ids=privacy.blacklist_bundle_ids,
            patterns=privacy.blacklist_patterns,
            app_names=privacy.blacklist_app_names,
        )

    def _note_browser_page(self, event: RawEvent) -> None:
        """Remember the page a browser app is showing. Called before any drop, so the URL of an excluded
        domain is recorded too (its own event is dropped, but the text typed after it must be checked).

        The URL watcher emits only when the frontmost browser's URL changes, and it polls every few seconds,
        so a value can lag a tab switch by about one poll interval. A browser with no URL seen yet is
        unknown and is not blocked.
        """
        if event.source not in ("browser", "browser_url"):
            return
        url = _event_url(event)
        app = (event.app_name or "").strip().casefold()
        if url and app:
            self._browser_pages[app] = url

    def _current_page_url(self, event: RawEvent) -> str | None:
        app = (event.app_name or "").strip().casefold()
        return self._browser_pages.get(app) if app else None

    def _count_gate_hit(self, event: RawEvent, reason: str) -> None:
        key = f"{event.source}:{reason}"
        self._gate_hits[key] = self._gate_hits.get(key, 0) + 1
        logger.debug("capture gate: %s (%s)", reason, event.source)  # never log content

    def _process_event(self, event: RawEvent):
        # Write gate (#28): global pause or a switched-off source writes nothing new.
        if not source_writes_allowed(event.source):
            self._count_gate_hit(event, "paused_or_disabled")
            return

        if self._storage_write_blocked():
            self._storage_dropped += 1
            return
        event.speaker = _derive_speaker(event)

        self._note_browser_page(event)
        if self._is_blacklisted(event):
            logger.debug(
                f"Event blacklisted: {event.process_name or event.app_name} ({event.source}/{event.event_type})"
            )
            return

        if _should_drop_browser_event(event, self.config.privacy.url_deny_hosts):
            logger.debug(
                "Event dropped by browser URL denylist: %s (%s/%s)",
                event.process_name or event.app_name,
                event.source,
                event.event_type,
            )
            return

        verdict = capture_gate(
            source=event.source,
            app_name=event.app_name,
            process_name=event.process_name,
            window_title=event.window_title,
            content_text=event.content_text,
            url=_event_url(event),
            title_deny_patterns=self.config.privacy.title_deny_patterns,
            page_url=self._current_page_url(event) if event.source in PAGE_SCOPED_STREAMS else None,
            url_deny_hosts=self.config.privacy.url_deny_hosts,
        )
        if verdict is not None:
            self._count_gate_hit(event, verdict.reason)
            if verdict.action != METADATA_ONLY:
                return

        if event.source != "window" and is_system_overlay(event.app_name, event.process_name):
            # Setup Assistant & co: keep what was typed/copied/read, but it belongs to no app (D27).
            event.app_name = None
            event.process_name = None

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
        text_dropped_reason: str | None = None
        if verdict is not None:  # metadata-only: keep app + time, null the content
            text_dropped_reason = verdict.reason
            result.content_text = None
            result.content_hash = None
            if verdict.reason != "terminal_app":  # title-based reasons: the title itself is the sensitive part
                result.window_title = None
            if result.source == "browser_url":
                result.metadata_json = _strip_metadata_urls(result.metadata_json)
        if result.source == "ax_text" and _is_terminal_app(result.app_name):
            if self.config.privacy.drop_terminal_text:
                result.content_text = None
                text_dropped_reason = "terminal_app"
            elif result.content_text:
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
        elif result.content_text:
            result.content_text = _sanitize_content_text(
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
        if result.source == "keyboard_chunk":
            result.content_text = _sanitize_content_text(
                result.content_text,
                redact_emails=self.config.privacy.redact_emails,
                redact_phones=self.config.privacy.redact_phones,
                redact_tokens=self.config.privacy.redact_tokens,
            )
            if result.app_name and result.app_name.strip().lower() not in _CHAT_STYLE_APPS:
                result.semantic_weight = 0.5
            else:
                result.semantic_weight = 1.0

        preserve_raw_browser_url = result.source == "browser_url"
        result.metadata_json = _sanitize_metadata_json(
            result.metadata_json,
            extra_fields={"text_dropped": text_dropped_reason} if text_dropped_reason else None,
            redact_emails=False if preserve_raw_browser_url else self.config.privacy.redact_emails,
            redact_phones=False if preserve_raw_browser_url else self.config.privacy.redact_phones,
            redact_tokens=False if preserve_raw_browser_url else self.config.privacy.redact_tokens,
        )

        if result.session_id is None and result.event_type not in {"idle_start", "idle_end"}:
            session_id = self._current_window_session_id()
            if session_id:
                result.session_id = session_id

        # 3. Session tracking
        session = self._aggregator.process(result)
        if session and result.session_id is None:
            result.session_id = session.id

        # 4. User presence
        result.user_present = 1 if is_user_present() else 0

        # 5. Persist raw event
        try:
            row_id = insert_raw_event(result)
        except sqlite3.OperationalError as exc:
            if not _is_disk_full(exc):
                raise
            self._enter_disk_full()
            return
        self._leave_disk_full()
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
        # === OCR watcher 已下线 2026-05-14 ===
        # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
        # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
        # 历史 raw_events 中 ocr_text_capture 数据保留可读
        return
        # ocr_watcher = self._watchers.get("ocr")
        # if ocr_watcher is None:
        #     return
        # # window_focus and window_title_changed both mean the frontmost context moved.
        # if event.source == "window" and is_window_session_event_type(event.event_type) and hasattr(ocr_watcher, "note_window_change"):
        #     ocr_watcher.note_window_change()
        #     return
        # if event.source == "window" and event.event_type == "window_focus_session" and hasattr(ocr_watcher, "note_window_change"):
        #     try:
        #         metadata = json.loads(event.metadata_json or "{}")
        #     except Exception:
        #         metadata = {}
        #     if metadata.get("reason") == "app_switch":
        #         ocr_watcher.note_window_change()

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

    def _storage_write_blocked(self) -> bool:
        return self._storage_error is not None and time.monotonic() < self._storage_next_probe_mono

    def _enter_disk_full(self) -> None:
        # Roll back the failed statement so the shared connection is not left mid-transaction.
        try:
            get_conn().rollback()
        except Exception:
            pass
        if self._storage_error is None:
            self._storage_error_since = datetime.now(timezone.utc).isoformat()
            logger.error("capture paused: database or disk is full; will probe again in %.0fs", self.DISK_FULL_PROBE_SEC)
        self._storage_error = "disk_full"
        self._storage_next_probe_mono = time.monotonic() + self.DISK_FULL_PROBE_SEC
        self._persist_storage_state()

    def _leave_disk_full(self) -> None:
        if self._storage_error is None:
            return
        logger.info("capture storage writes recovered after disk_full (dropped %d events)", self._storage_dropped)
        self._storage_error = None
        self._storage_error_since = None
        self._persist_storage_state()

    def _persist_storage_state(self) -> None:
        # Best effort: when the disk is really full this write can fail too; the in-process state is authoritative.
        try:
            self._persist_runtime_state(force=True)
        except Exception as exc:
            logger.warning("capture storage state not persisted: %s", type(exc).__name__)

    def _storage_status(self) -> dict:
        if self._storage_error is None:
            return {"status": "ok"}
        return {
            "status": "error",
            "reason": self._storage_error,
            "since": self._storage_error_since,
            "dropped_events": self._storage_dropped,
        }

    def _runtime_snapshot(self) -> dict:
        multi_source_counts = {
            source: self._source_counts.get(source, 0)
            for source in ("ax_text", "ocr_text", "clipboard", "manual", "keyboard_chunk")
        }
        last_seen = {
            source: self._source_last_event_at.get(source)
            for source in ("ax_text", "ocr_text", "clipboard", "manual", "keyboard_chunk")
            if self._source_last_event_at.get(source)
        }
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "host_executable": os.path.realpath(os.sys.executable),
            "running": self._running.is_set(),
            "paused": self._paused.is_set(),
            "disabled_sources": sorted(disabled_sources()),
            "watchers": {
                name: watcher.health() | {"heartbeat_timeout_sec": watcher.HEARTBEAT_TIMEOUT_SEC}
                for name, watcher in self._watchers.items()
            },
            "queue_size": self._queue.qsize(),
            "multi_source_counts": multi_source_counts,
            "gate_hits": dict(self._gate_hits),
            "last_seen": last_seen,
            "storage": self._storage_status(),
        }

    def _persist_runtime_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._runtime_state_last_persist) < 2.0:
            return
        snapshot = self._runtime_snapshot()
        set_state("capture_runtime", json.dumps(snapshot, ensure_ascii=False))
        self._runtime_state_last_persist = now
