from __future__ import annotations

import queue
import subprocess
import time
from typing import Optional

from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_browser_tab_event
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.browser")

DEFAULT_SUPPORTED_BROWSERS = (
    "Safari",
    "Google Chrome",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
)


def _get_frontmost_app_name() -> Optional[str]:
    """Return the frontmost application name using AppKit when available."""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else None
    except Exception as exc:
        logger.debug("BrowserWatcher frontmost app unavailable: %s", exc)
        return None


def _build_applescript(browser_name: str) -> str:
    if browser_name == "Safari":
        return f'''
tell application "{browser_name}"
    if not (exists front window) then return ""
    try
        set theTab to current tab of front window
        set theUrl to ""
        set theTitle to ""
        try
            set theUrl to URL of theTab
        end try
        try
            set theTitle to name of theTab
        end try
        if theUrl is missing value then set theUrl to ""
        if theTitle is missing value then set theTitle to ""
        return theUrl & linefeed & theTitle
    on error
        return ""
    end try
end tell
'''

    return f'''
tell application "{browser_name}"
    if not (exists front window) then return ""
    try
        set theTab to active tab of front window
        set theUrl to ""
        set theTitle to ""
        try
            set theUrl to URL of theTab
        end try
        try
            set theTitle to title of theTab
        end try
        if theUrl is missing value then set theUrl to ""
        if theTitle is missing value then set theTitle to ""
        return theUrl & linefeed & theTitle
    on error
        return ""
    end try
end tell
'''


def _is_permission_error(stderr: str, returncode: int) -> bool:
    stderr_lower = stderr.lower()
    return returncode != 0 and (
        "-1743" in stderr_lower
        or "not authorized" in stderr_lower
        or "apple events" in stderr_lower
    )


def _parse_browser_output(stdout: str) -> tuple[str, str] | None:
    payload = (stdout or "").replace("\r\n", "\n").replace("\r", "\n")
    if not payload.strip():
        return None
    url, title = payload.split("\n", 1) if "\n" in payload else (payload, "")
    url = url.strip()
    title = title.strip()
    if not url and not title:
        return None
    return url, title


class BrowserWatcher(BaseWatcher):
    name = "browser"

    def __init__(
        self,
        event_queue: queue.Queue,
        poll_interval_sec: float = 1.0,
        supported_browsers: list[str] | tuple[str, ...] | None = None,
    ):
        super().__init__(event_queue)
        self._poll_interval = poll_interval_sec
        self._supported_browsers = tuple(supported_browsers or DEFAULT_SUPPORTED_BROWSERS)
        self._last_seen: dict[str, tuple[str, str]] = {}
        self._disabled_browsers: set[str] = set()

    def capture_once(self):
        browser_name = _get_frontmost_app_name()
        if not browser_name or browser_name not in self._supported_browsers:
            return None
        if browser_name in self._disabled_browsers:
            return None

        payload = self._read_browser_tab(browser_name)
        if payload is None:
            return None

        url, title = payload
        normalized_title = title or None
        snapshot = (url, title)
        if self._last_seen.get(browser_name) == snapshot:
            return None
        self._last_seen[browser_name] = snapshot
        return normalize_browser_tab_event(
            url=url,
            title=normalized_title,
            browser_name=browser_name,
        )

    def _read_browser_tab(self, browser_name: str) -> tuple[str, str] | None:
        script = _build_applescript(browser_name)
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            logger.debug("BrowserWatcher osascript failed for %s: %s", browser_name, exc)
            return None

        stderr = (result.stderr or "").strip()
        if _is_permission_error(stderr, result.returncode):
            self._disable_browser(browser_name, stderr)
            return None

        if result.returncode != 0:
            logger.debug(
                "BrowserWatcher osascript returned %s for %s: %s",
                result.returncode,
                browser_name,
                stderr,
            )
            return None

        return _parse_browser_output(result.stdout or "")

    def _disable_browser(self, browser_name: str, stderr: str) -> None:
        if browser_name in self._disabled_browsers:
            return
        self._disabled_browsers.add(browser_name)
        logger.warning(
            "BrowserWatcher: disabling %s after AppleEvents permission failure: %s",
            browser_name,
            stderr or "not authorized",
        )

    def _run(self):
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(self._poll_interval)
                continue
            try:
                event = self.capture_once()
                if event is not None:
                    self.emit(event)
            except Exception as exc:
                logger.error("BrowserWatcher error: %s", exc)
            time.sleep(self._poll_interval)
