"""macOS permission prompts.

Trigger native TCC authorization dialogs for the watchers that need them.
Uses the *Request*/*WithOptions* API variants (not the *Preflight* ones) so
macOS shows the official permission prompt when state is `undetermined`.

When state is already `denied`, these APIs return silently — the system will
not re-prompt. Recovery in that case requires either user action (manually
toggle in System Settings) or a TCC reset (`tccutil reset` on install).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from keypulse.utils.logging import get_logger

logger = get_logger("permissions")


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"  # API not importable (non-macOS or missing pyobjc)


@dataclass(frozen=True)
class PermissionSpec:
    key: str
    label: str
    settings_pane: str  # x-apple.systempreferences anchor
    check: Callable[[], PermissionStatus]
    request: Callable[[], PermissionStatus]


def _accessibility_check() -> PermissionStatus:
    try:
        from ApplicationServices import AXIsProcessTrusted
    except Exception:
        return PermissionStatus.UNAVAILABLE
    return PermissionStatus.GRANTED if AXIsProcessTrusted() else PermissionStatus.DENIED


def _accessibility_request() -> PermissionStatus:
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate, kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks
        import objc
    except Exception:
        return PermissionStatus.UNAVAILABLE
    try:
        prompt_key = objc.lookUpClass("NSString").stringWithString_("AXTrustedCheckOptionPrompt")
        options = {prompt_key: True}
        trusted = bool(AXIsProcessTrustedWithOptions(options))
    except Exception as exc:
        logger.warning("accessibility prompt failed: %s", exc)
        return _accessibility_check()
    return PermissionStatus.GRANTED if trusted else PermissionStatus.DENIED


def _listen_event_check() -> PermissionStatus:
    try:
        import Quartz
    except Exception:
        return PermissionStatus.UNAVAILABLE
    preflight = getattr(Quartz, "CGPreflightListenEventAccess", None)
    if not callable(preflight):
        return PermissionStatus.UNAVAILABLE
    return PermissionStatus.GRANTED if preflight() else PermissionStatus.DENIED


def _listen_event_request() -> PermissionStatus:
    try:
        import Quartz
    except Exception:
        return PermissionStatus.UNAVAILABLE
    request = getattr(Quartz, "CGRequestListenEventAccess", None)
    if not callable(request):
        return PermissionStatus.UNAVAILABLE
    granted = bool(request())
    return PermissionStatus.GRANTED if granted else PermissionStatus.DENIED


def _screen_capture_check() -> PermissionStatus:
    try:
        import Quartz
    except Exception:
        return PermissionStatus.UNAVAILABLE
    preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if not callable(preflight):
        return PermissionStatus.UNAVAILABLE
    return PermissionStatus.GRANTED if preflight() else PermissionStatus.DENIED


def _screen_capture_request() -> PermissionStatus:
    try:
        import Quartz
    except Exception:
        return PermissionStatus.UNAVAILABLE
    request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
    if not callable(request):
        return PermissionStatus.UNAVAILABLE
    granted = bool(request())
    return PermissionStatus.GRANTED if granted else PermissionStatus.DENIED


PERMISSION_SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(
        key="accessibility",
        label="辅助功能（Accessibility）",
        settings_pane="x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        check=_accessibility_check,
        request=_accessibility_request,
    ),
    PermissionSpec(
        key="input_monitoring",
        label="输入监控（Input Monitoring）",
        settings_pane="x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
        check=_listen_event_check,
        request=_listen_event_request,
    ),
)


def trigger_required_prompts() -> dict[str, PermissionStatus]:
    """Trigger native authorization prompts for any permission not yet granted.

    Safe to call on daemon startup: API returns silently when state is `granted`
    or `denied`; only `undetermined` actually shows a dialog.
    """
    results: dict[str, PermissionStatus] = {}
    for spec in PERMISSION_SPECS:
        current = spec.check()
        if current == PermissionStatus.GRANTED:
            results[spec.key] = PermissionStatus.GRANTED
            continue
        if current == PermissionStatus.UNAVAILABLE:
            results[spec.key] = PermissionStatus.UNAVAILABLE
            logger.info("permission %s: API unavailable (non-macOS or pyobjc missing)", spec.key)
            continue
        logger.info("permission %s: not granted, triggering native prompt", spec.key)
        results[spec.key] = spec.request()
    return results


def open_settings_pane(spec: PermissionSpec) -> None:
    subprocess.run(["open", spec.settings_pane], check=False)


def find_spec(key: str) -> PermissionSpec | None:
    for spec in PERMISSION_SPECS:
        if spec.key == key:
            return spec
    return None
