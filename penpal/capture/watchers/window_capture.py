from __future__ import annotations


def capture_frontmost_window_image(appkit_module=None, quartz_module=None):
    try:
        appkit = appkit_module
        if appkit is None:
            import AppKit as appkit

        quartz = quartz_module
        if quartz is None:
            import Quartz as quartz
    except Exception:
        return None

    try:
        preflight = getattr(quartz, "CGPreflightScreenCaptureAccess", None)
        if callable(preflight) and not bool(preflight()):
            return None
    except Exception:
        return None

    try:
        app = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return None
        pid = app.processIdentifier()

        window_info = quartz.CGWindowListCopyWindowInfo(
            quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements,
            quartz.kCGNullWindowID,
        ) or []
        for window in window_info:
            owner_pid = window.get(quartz.kCGWindowOwnerPID)
            layer = window.get(quartz.kCGWindowLayer, 0)
            window_number = window.get(quartz.kCGWindowNumber)
            if owner_pid != pid or not window_number or layer != 0:
                continue

            image = quartz.CGWindowListCreateImage(
                getattr(quartz, "CGRectNull", ((0, 0), (0, 0))),
                quartz.kCGWindowListOptionIncludingWindow,
                window_number,
                getattr(quartz, "kCGWindowImageBoundsIgnoreFraming", 0),
            )
            if image is not None:
                return image
    except Exception:
        return None

    return None
