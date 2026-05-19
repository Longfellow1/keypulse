from __future__ import annotations

import queue
import subprocess
import time

from keypulse.capabilities.builtin.browser_automation import (
    is_browser_automation_denied,
    mark_browser_automation_denied,
)
from keypulse.capture.base import BaseWatcher
from keypulse.capture.normalizer import normalize_browser_url_event
from keypulse.capture.watchers.browser import _get_frontmost_app_name
from keypulse.utils.logging import get_logger

logger = get_logger("watcher.browser_url")


DEFAULT_SUPPORTED_BROWSERS = (
    "Safari",
    "Google Chrome",
    "Arc",
    "Microsoft Edge",
    "Brave Browser",
)

_CHROME_DIALECT_BROWSERS = {
    "Google Chrome",
    "Arc",
    "Microsoft Edge",
    "Brave Browser",
}


def _build_applescript(browser_name: str) -> str:
    if browser_name == "Safari":
        return '''
tell application "Safari"
    if (count of windows) > 0 then
        set theURL to URL of current tab of front window
        set theTitle to name of current tab of front window
        return theURL & "|||" & theTitle
    end if
end tell
'''

    if browser_name in _CHROME_DIALECT_BROWSERS:
        return f'''
tell application "{browser_name}"
    if (count of windows) > 0 then
        set theURL to URL of active tab of front window
        set theTitle to title of active tab of front window
        return theURL & "|||" & theTitle
    end if
end tell
'''
    return ""


def _parse_browser_output(stdout: str) -> tuple[str, str] | None:
    payload = (stdout or "").strip()
    if not payload:
        return None
    if "|||" in payload:
        url, title = payload.split("|||", 1)
    elif "\n" in payload:
        url, title = payload.split("\n", 1)
    else:
        url, title = payload, ""
    normalized_url = str(url or "").strip()
    normalized_title = str(title or "").strip()
    if not normalized_url:
        return None
    return normalized_url, normalized_title


class BrowserUrlWatcher(BaseWatcher):
    name = "browser_url"
    HEARTBEAT_TIMEOUT_SEC = 300.0

    def __init__(
        self,
        event_queue: queue.Queue,
        *,
        poll_interval_sec: float = 3.0,
        supported_browsers: list[str] | tuple[str, ...] | None = None,
        emit_on_url_change_only: bool = True,
    ) -> None:
        super().__init__(event_queue)
        self._poll_interval = max(float(poll_interval_sec), 0.1)
        self._supported_browsers = tuple(supported_browsers or DEFAULT_SUPPORTED_BROWSERS)
        self._emit_on_url_change_only = bool(emit_on_url_change_only)
        self._last_url_by_browser: dict[str, str] = {}
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
        if self._emit_on_url_change_only and self._last_url_by_browser.get(browser_name) == url:
            return None
        self._last_url_by_browser[browser_name] = url

        return normalize_browser_url_event(
            url=url,
            title=title or None,
            browser_name=browser_name,
        )

    def _read_browser_tab(self, browser_name: str) -> tuple[str, str] | None:
        script = _build_applescript(browser_name)
        if not script:
            return None
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
        except subprocess.TimeoutExpired:
            return None
        except Exception as exc:
            logger.debug("BrowserUrlWatcher osascript failed for %s: %s", browser_name, exc)
            return None

        stderr = (result.stderr or "").strip()
        if is_browser_automation_denied(stderr, result.returncode):
            self._disable_browser(browser_name, stderr or f"exit={result.returncode}")
            return None
        if result.returncode != 0:
            return None
        return _parse_browser_output(result.stdout or "")

    def _disable_browser(self, browser_name: str, reason: str) -> None:
        if browser_name in self._disabled_browsers:
            return
        self._disabled_browsers.add(browser_name)
        mark_browser_automation_denied(browser_name)
        logger.warning(
            "BrowserUrlWatcher: disabling %s after AppleEvents failure: %s",
            browser_name,
            reason,
        )

    def _run(self) -> None:
        while self._running.is_set():
            if self._paused.is_set():
                time.sleep(self._poll_interval)
                continue
            try:
                event = self.capture_once()
                if event is not None:
                    self.emit(event)
                else:
                    self.beat()
            except Exception as exc:
                logger.error("BrowserUrlWatcher error: %s", exc)
            time.sleep(self._poll_interval)
