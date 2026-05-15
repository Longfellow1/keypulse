from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Protocol

from keypulse.sources.registry import list_sources, read_all
from keypulse.sources.types import DataSource, SemanticEvent
from keypulse.utils.logging import get_logger

LOGGER = get_logger("sources.scheduler")


def _normalize_app_token(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _expand_app_tokens(value: str) -> set[str]:
    normalized = _normalize_app_token(value)
    if not normalized:
        return set()
    tokens = {normalized, normalized.replace(" ", "")}
    if "." in normalized:
        tail = normalized.rsplit(".", 1)[-1]
        if tail:
            tokens.add(tail)
    return {token for token in tokens if token}


class WorkspaceEventSource(Protocol):
    def start(
        self,
        *,
        on_app_launch: Callable[[str, str], None],
        on_app_quit: Callable[[str, str], None],
        on_unlock: Callable[[], None],
    ) -> None:
        ...

    def stop(self) -> None:
        ...


class _NSWorkspaceEventSource:
    def __init__(self) -> None:
        self._on_app_launch: Callable[[str, str], None] | None = None
        self._on_app_quit: Callable[[str, str], None] | None = None
        self._on_unlock: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._start_error: BaseException | None = None

    def start(
        self,
        *,
        on_app_launch: Callable[[str, str], None],
        on_app_quit: Callable[[str, str], None],
        on_unlock: Callable[[], None],
    ) -> None:
        if self._thread is not None:
            return
        self._on_app_launch = on_app_launch
        self._on_app_quit = on_app_quit
        self._on_unlock = on_unlock
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="sources-nsworkspace")
        self._thread.start()
        self._ready.wait(timeout=3.0)
        if self._start_error is not None:
            raise RuntimeError("NSWorkspace event source start failed") from self._start_error

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run_loop(self) -> None:
        observer = None
        center = None
        try:
            import objc
            from AppKit import (
                NSWorkspace,
                NSWorkspaceApplicationKey,
                NSWorkspaceDidLaunchApplicationNotification,
                NSWorkspaceDidTerminateApplicationNotification,
                NSWorkspaceSessionDidBecomeActiveNotification,
            )
            from Foundation import NSDate, NSDefaultRunLoopMode, NSObject, NSRunLoop

            class WorkspaceNotificationObserver(NSObject):
                def initWithCallbacks_(self, callbacks):
                    self = objc.super(WorkspaceNotificationObserver, self).init()
                    if self is None:
                        return None
                    self._on_launch = callbacks[0]
                    self._on_quit = callbacks[1]
                    self._on_unlock = callbacks[2]
                    return self

                @objc.python_method
                def _payload(self, notification):
                    user_info = notification.userInfo() or {}
                    app = user_info.get(NSWorkspaceApplicationKey)
                    bundle_id = ""
                    process_name = ""
                    if app is not None:
                        try:
                            bundle_id = str(app.bundleIdentifier() or "")
                        except Exception:
                            bundle_id = ""
                        try:
                            process_name = str(app.localizedName() or "")
                        except Exception:
                            process_name = ""
                    if not bundle_id:
                        bundle_id = str(user_info.get("NSApplicationBundleIdentifier") or "")
                    if not process_name:
                        process_name = str(user_info.get("NSApplicationName") or "")
                    return bundle_id, process_name

                def handleLaunch_(self, notification):
                    bundle_id, process_name = self._payload(notification)
                    self._on_launch(bundle_id, process_name)

                def handleTerminate_(self, notification):
                    bundle_id, process_name = self._payload(notification)
                    self._on_quit(bundle_id, process_name)

                def handleSessionActive_(self, notification):
                    _ = notification
                    self._on_unlock()

            workspace = NSWorkspace.sharedWorkspace()
            center = workspace.notificationCenter()
            observer = WorkspaceNotificationObserver.alloc().initWithCallbacks_(
                (
                    self._on_app_launch or (lambda _bundle_id, _process_name: None),
                    self._on_app_quit or (lambda _bundle_id, _process_name: None),
                    self._on_unlock or (lambda: None),
                )
            )
            center.addObserver_selector_name_object_(
                observer,
                "handleLaunch:",
                NSWorkspaceDidLaunchApplicationNotification,
                None,
            )
            center.addObserver_selector_name_object_(
                observer,
                "handleTerminate:",
                NSWorkspaceDidTerminateApplicationNotification,
                None,
            )
            center.addObserver_selector_name_object_(
                observer,
                "handleSessionActive:",
                NSWorkspaceSessionDidBecomeActiveNotification,
                None,
            )
            LOGGER.info("sources scheduler NSWorkspace event source started")
            self._ready.set()

            run_loop = NSRunLoop.currentRunLoop()
            while not self._stop_event.is_set():
                run_loop.runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    NSDate.dateWithTimeIntervalSinceNow_(0.2),
                )
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self._start_error = exc
            LOGGER.exception("NSWorkspace event source crashed")
            self._ready.set()
        finally:  # pragma: no cover - runtime clean up
            if center is not None and observer is not None:
                try:
                    center.removeObserver_(observer)
                except Exception:
                    LOGGER.debug("failed to remove NSWorkspace observer")


class SourcesScheduler:
    def __init__(
        self,
        *,
        enabled: bool = True,
        poll_interval_sec: int = 600,
        debounce_sec: int = 300,
        list_sources_fn: Callable[[], list[DataSource]] = list_sources,
        read_all_fn: Callable[..., Iterator[SemanticEvent]] = read_all,
        event_source_factory: Callable[[], WorkspaceEventSource | None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = bool(enabled)
        self._poll_interval_sec = max(float(poll_interval_sec), 0.05)
        self._debounce_sec = max(float(debounce_sec), 0.0)
        self._list_sources_fn = list_sources_fn
        self._read_all_fn = read_all_fn
        self._event_source_factory = event_source_factory or (lambda: _NSWorkspaceEventSource())
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._monotonic_fn = monotonic_fn

        self._running = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._executor: ThreadPoolExecutor | None = None
        self._always_thread: threading.Thread | None = None
        self._event_source: WorkspaceEventSource | None = None

        self._always_plugins: set[str] = set()
        self._unlock_plugins: set[str] = set()
        self._launch_routes: dict[str, set[str]] = {}
        self._quit_routes: dict[str, set[str]] = {}

        self._last_trigger_mono: dict[str, float] = {}
        self._last_success_utc: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            LOGGER.info("sources scheduler disabled by config")
            return
        if self._running.is_set():
            return

        self._rebuild_routes()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sources-scan")
        self._stop_event.clear()
        self._running.set()

        if self._always_plugins:
            self._always_thread = threading.Thread(
                target=self._always_loop,
                daemon=True,
                name="sources-always",
            )
            self._always_thread.start()

        if self._launch_routes or self._quit_routes or self._unlock_plugins:
            try:
                self._event_source = self._event_source_factory()
                if self._event_source is not None:
                    self._event_source.start(
                        on_app_launch=self._on_app_launch,
                        on_app_quit=self._on_app_quit,
                        on_unlock=self._on_unlock,
                    )
            except Exception:
                LOGGER.exception("failed to start NSWorkspace event source; event-triggered scans disabled")
                self._event_source = None

        LOGGER.info(
            "sources scheduler started enabled=%s poll_interval_sec=%s debounce_sec=%s always=%s app_running_routes=%s after_quit_routes=%s after_unlock=%s",
            self._enabled,
            self._poll_interval_sec,
            self._debounce_sec,
            sorted(self._always_plugins),
            {k: sorted(v) for k, v in self._launch_routes.items()},
            {k: sorted(v) for k, v in self._quit_routes.items()},
            sorted(self._unlock_plugins),
        )

    def stop(self) -> None:
        if not self._running.is_set() and self._executor is None:
            return

        self._running.clear()
        self._stop_event.set()

        if self._event_source is not None:
            try:
                self._event_source.stop()
            except Exception:
                LOGGER.exception("failed to stop NSWorkspace event source")
            finally:
                self._event_source = None

        if self._always_thread is not None:
            self._always_thread.join(timeout=2.0)
            self._always_thread = None

        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None

        LOGGER.info("sources scheduler stopped")

    def _rebuild_routes(self) -> None:
        self._always_plugins.clear()
        self._unlock_plugins.clear()
        self._launch_routes.clear()
        self._quit_routes.clear()

        try:
            plugins = self._list_sources_fn()
        except Exception:
            LOGGER.exception("failed to list sources for scheduler")
            plugins = []

        for plugin in plugins:
            name = str(getattr(plugin, "name", "") or "")
            if not name:
                continue
            liveness = str(getattr(plugin, "liveness", "") or "").strip()
            app_hints = tuple(getattr(plugin, "app_hints", ()) or ())

            if liveness == "always":
                self._always_plugins.add(name)
                continue

            if liveness == "after_unlock":
                self._unlock_plugins.add(name)
                continue

            if liveness not in {"app_running", "after_app_quit"}:
                continue

            route_map = self._launch_routes if liveness == "app_running" else self._quit_routes
            for hint in app_hints:
                for token in _expand_app_tokens(str(hint)):
                    route_map.setdefault(token, set()).add(name)

    def _always_loop(self) -> None:
        while self._running.is_set():
            for plugin_name in sorted(self._always_plugins):
                if not self._running.is_set():
                    break
                self._schedule_scan(plugin_name, reason="always_interval")

            if self._stop_event.wait(timeout=self._poll_interval_sec):
                break

    def _on_app_launch(self, bundle_id: str, process_name: str) -> None:
        matched = self._match_plugins(self._launch_routes, bundle_id=bundle_id, process_name=process_name)
        for plugin_name in sorted(matched):
            self._schedule_scan(
                plugin_name,
                reason=f"app_launch:{bundle_id or '-'}:{process_name or '-'}",
            )

    def _on_app_quit(self, bundle_id: str, process_name: str) -> None:
        matched = self._match_plugins(self._quit_routes, bundle_id=bundle_id, process_name=process_name)
        for plugin_name in sorted(matched):
            self._schedule_scan(
                plugin_name,
                reason=f"app_quit:{bundle_id or '-'}:{process_name or '-'}",
            )

    def _on_unlock(self) -> None:
        for plugin_name in sorted(self._unlock_plugins):
            self._schedule_scan(plugin_name, reason="after_unlock")

    def _match_plugins(
        self,
        route_map: dict[str, set[str]],
        *,
        bundle_id: str,
        process_name: str,
    ) -> set[str]:
        tokens: set[str] = set()
        for candidate in (bundle_id, process_name):
            tokens.update(_expand_app_tokens(candidate))

        matched: set[str] = set()
        for token in tokens:
            matched.update(route_map.get(token, set()))
        return matched

    def _schedule_scan(self, plugin_name: str, *, reason: str) -> None:
        if not self._running.is_set():
            return
        if not self._check_and_mark_debounce(plugin_name):
            LOGGER.debug("sources scheduler debounce skip plugin=%s reason=%s", plugin_name, reason)
            return

        if self._executor is None:
            return
        self._executor.submit(self._scan_plugin, plugin_name, reason)

    def _check_and_mark_debounce(self, plugin_name: str) -> bool:
        now_mono = self._monotonic_fn()
        with self._lock:
            last_mono = self._last_trigger_mono.get(plugin_name)
            if last_mono is not None and (now_mono - last_mono) < self._debounce_sec:
                return False
            self._last_trigger_mono[plugin_name] = now_mono
        return True

    def _scan_plugin(self, plugin_name: str, reason: str) -> None:
        now = self._as_utc(self._now_fn())
        with self._lock:
            since = self._last_success_utc.get(plugin_name)
        if since is None:
            since = now - timedelta(seconds=self._poll_interval_sec)

        events = 0
        try:
            for _ in self._read_all_fn(
                since,
                now,
                source=plugin_name,
                persist_raw_events=True,
            ):
                events += 1
            with self._lock:
                self._last_success_utc[plugin_name] = now
            LOGGER.info(
                "sources scheduler scan done plugin=%s reason=%s events=%s since=%s until=%s",
                plugin_name,
                reason,
                events,
                since.isoformat(),
                now.isoformat(),
            )
        except Exception:
            LOGGER.exception("sources scheduler scan failed plugin=%s reason=%s", plugin_name, reason)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["SourcesScheduler", "WorkspaceEventSource"]
