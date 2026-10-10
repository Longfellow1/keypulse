"""NSWorkspace app launch/quit/unlock event source (macOS only; capture layer)."""
from __future__ import annotations

import threading
from typing import Callable

from keypulse.utils.logging import get_logger

LOGGER = get_logger("sources.scheduler")


class NSWorkspaceEventSource:
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
            # NSDistributedNotificationCenter observers do not register as
            # input sources, so runMode_beforeDate_ returns immediately
            # instead of blocking until the deadline. Without an explicit
            # sleep this loop spins (one thread pinned at 100% GIL),
            # eating ~60% of total daemon CPU. The wait both throttles the
            # pump rate and wakes promptly on shutdown.
            while not self._stop_event.is_set():
                run_loop.runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    NSDate.dateWithTimeIntervalSinceNow_(0.2),
                )
                if self._stop_event.wait(0.2):
                    break
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
