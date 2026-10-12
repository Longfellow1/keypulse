"""pyobjc adapter for the AX spike (#64). The only module that calls AX APIs.

Every pyobjc import is inside a method, so importing this module works without macOS or pyobjc.
Unverified against real AX in this branch (no permission was granted and no app was run):
  - CFHash from CoreFoundation as the element identity,
  - the return shape of AXValueGetValue (pyobjc may return (ok, CFRange) or the range alone),
  - which attributes Electron apps expose after AXManualAccessibility is set.
Those points are the ones to check first on the first real run.
"""
from __future__ import annotations

from typing import Any

from keypulse.capture.ax_spike import (
    R_NO_FOCUS,
    R_PERMISSION,
    FocusRead,
    TextRead,
    classify_element,
)

# AXError codes (ApplicationServices/AXError.h).
_AX_SUCCESS = 0
_AX_API_DISABLED = -25211
_AX_NO_VALUE = -25212
# kAXValueCFRangeType
_AX_VALUE_CFRANGE = 4


def _copy_attr(ax, element: Any, attribute: str) -> tuple[int, Any]:
    try:
        return ax.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return -1, None


def _str_attr(ax, element: Any, attribute: str) -> str | None:
    error, value = _copy_attr(ax, element, attribute)
    if error != _AX_SUCCESS or value is None:
        return None
    return value if isinstance(value, str) else None


class PyObjCAXAdapter:
    """Real adapter. Requires macOS, pyobjc, and Accessibility permission for the terminal."""

    def is_trusted(self) -> bool:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())

    def find_app_pid(self, app_name: str) -> int | None:
        import AppKit

        target = app_name.casefold()
        fallback: int | None = None
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            name = (app.localizedName() or "").casefold()
            bundle = (app.bundleIdentifier() or "").casefold()
            if target not in (name, bundle):
                continue
            pid = int(app.processIdentifier())
            # NSApplicationActivationPolicyRegular == 0: prefer the user-facing process over helpers.
            if app.activationPolicy() == 0:
                return pid
            fallback = fallback if fallback is not None else pid
        return fallback

    def enable_accessibility(self, pid: int, app_name: str) -> dict[str, int]:
        import ApplicationServices as ax

        app_element = ax.AXUIElementCreateApplication(pid)
        attributes = ["AXManualAccessibility"]
        if "chrome" in app_name.casefold():
            attributes.append("AXEnhancedUserInterface")
        codes: dict[str, int] = {}
        for attribute in attributes:
            try:
                codes[attribute] = int(ax.AXUIElementSetAttributeValue(app_element, attribute, True))
            except Exception:
                codes[attribute] = -1
        return codes

    def read_focused(self, pid: int) -> FocusRead:
        import ApplicationServices as ax

        app_element = ax.AXUIElementCreateApplication(pid)
        error, focused = _copy_attr(ax, app_element, "AXFocusedUIElement")
        if error == _AX_API_DISABLED:
            return FocusRead(ok=False, reason=R_PERMISSION)
        if error != _AX_SUCCESS or focused is None:
            return FocusRead(ok=False, reason=R_NO_FOCUS)

        role = _str_attr(ax, focused, "AXRole")
        subrole = _str_attr(ax, focused, "AXSubrole")
        return FocusRead(
            ok=True,
            role=role,
            subrole=subrole,
            element_key=self._element_key(focused),
            handle=focused,
        )

    def read_text(self, read: FocusRead) -> TextRead:
        # Called only after classify_element() accepted the element, so secure fields never reach here.
        import ApplicationServices as ax

        focused = read.handle
        if classify_element(read.role, read.subrole) is not None:  # defensive second check
            return TextRead(value=None, marked_range=None, marked_supported=False, window_title=None)

        names = self._attribute_names(ax, focused)
        marked_supported = "AXMarkedTextRange" in names
        marked_range = self._cfrange_attr(ax, focused, "AXMarkedTextRange") if marked_supported else None

        value_error, value = _copy_attr(ax, focused, "AXValue")
        text = value if (value_error == _AX_SUCCESS and isinstance(value, str)) else None

        window_title = None
        app_error, app_element = _copy_attr(ax, focused, "AXWindow")
        if app_error == _AX_SUCCESS and app_element is not None:
            window_title = _str_attr(ax, app_element, "AXTitle")

        return TextRead(
            value=text,
            marked_range=marked_range,
            marked_supported=marked_supported,
            window_title=window_title,
        )

    @staticmethod
    def _element_key(element: Any) -> Any:
        try:
            from CoreFoundation import CFHash

            return int(CFHash(element))
        except Exception:
            # Unknown identity: the tracker resets its baseline on every poll, so no false deltas appear.
            return None

    @staticmethod
    def _attribute_names(ax, element: Any) -> set[str]:
        try:
            error, names = ax.AXUIElementCopyAttributeNames(element, None)
        except Exception:
            return set()
        if error != _AX_SUCCESS or not names:
            return set()
        return {str(name) for name in names}

    @staticmethod
    def _cfrange_attr(ax, element: Any, attribute: str) -> tuple[int, int] | None:
        error, value = _copy_attr(ax, element, attribute)
        if error == _AX_NO_VALUE or error != _AX_SUCCESS or value is None:
            return None
        try:
            result = ax.AXValueGetValue(value, _AX_VALUE_CFRANGE, None)
        except Exception:
            return None
        # pyobjc may return (ok, CFRange) for an out-parameter. Accept both shapes.
        if isinstance(result, tuple):
            if not result[0]:
                return None
            cf_range = result[1]
        else:
            cf_range = result
        try:
            return int(cf_range.location), int(cf_range.length)
        except Exception:
            return None
