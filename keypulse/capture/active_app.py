from __future__ import annotations

from typing import Optional

from keypulse.utils.logging import get_logger

logger = get_logger("capture.active_app")

SYSTEM_OVERLAY_APPS = frozenset({
    "loginwindow", "ScreenSaverEngine", "Dock",
    "Notification Center", "Control Center",
})
# Backward-compatible alias for any internal usage.
_SYSTEM_OVERLAY_APPS = SYSTEM_OVERLAY_APPS

_last_known_app: Optional[str] = None
_last_known_pid: Optional[int] = None


def get_active_app() -> tuple[Optional[str], Optional[int]]:
    """Returns (app_name, pid). When system overlay (loginwindow/ScreenSaver/Dock)
    is in front, fall back to the last known real user-facing app.
    Returns (None, None) only if no real app has ever been seen.
    """
    global _last_known_app, _last_known_pid
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return _last_known_app, _last_known_pid
        name = app.localizedName()
        pid = app.processIdentifier()
        if name in _SYSTEM_OVERLAY_APPS:
            return _last_known_app, _last_known_pid
        _last_known_app = name
        _last_known_pid = pid
        return name, pid
    except Exception as exc:
        logger.debug("get_active_app error: %s", exc)
        return _last_known_app, _last_known_pid


def is_system_overlay_active() -> bool:
    """True if frontmost is currently a system overlay (loginwindow/screensaver/Dock).
    Use this to decide whether to skip content reads (ax_text)."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return True
        return app.localizedName() in _SYSTEM_OVERLAY_APPS
    except Exception:
        return False


def reset_for_test() -> None:
    """Test helper to clear cached state."""
    global _last_known_app, _last_known_pid
    _last_known_app = None
    _last_known_pid = None
