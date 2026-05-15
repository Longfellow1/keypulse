from __future__ import annotations

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts, watcher_has_recent_emit, watcher_healthy


_SCREEN_RECORDING_ACTION = (
    "open://x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)


class ScreenRecordingPermissionCapability(Capability):
    """Surface macOS Screen Recording permission status.

    Affects OCR / window screenshot capture. Like accessibility, macOS
    caches `CGPreflightScreenCaptureAccess()` per process — so a fresh
    grant doesn't reflect until the daemon process restarts. We still
    expose the action URL so the user can grant the permission in one
    click; supervisor surfaces a "restart daemon" CTA via the existing
    HUD restart button.
    """

    name = "screen_recording_permission"
    level_when_failed = "warn"  # warn (not err): OCR is opt-in tier-2 capture
    label_when_failed = "采集异常"
    error_codes = {"missing_screen_capture", "screen_capture_denied"}

    _HINTS = {
        "missing_screen_capture": "Quartz framework 缺失，无法检查屏幕录制权限",
        "screen_capture_denied": "屏幕录制权限未授权（OCR 不可用），请前往系统设置 → 隐私 → 屏幕录制",
    }

    def _probe(self) -> CheckResult:
        try:
            import Quartz  # type: ignore
        except Exception:
            return CheckResult(
                ok=False,
                code="missing_screen_capture",
                hint=self._HINTS["missing_screen_capture"],
            )

        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if not callable(preflight):
            # Older macOS lacks the preflight API — assume granted to avoid
            # false positives. The actual capture call would surface failures.
            return CheckResult(ok=True, code="ok")

        try:
            granted = bool(preflight())
        except Exception:
            granted = False

        if not granted:
            if watcher_healthy("ocr") and watcher_has_recent_emit("ocr", 3600.0):
                return CheckResult(
                    ok=True,
                    code="ok",
                    hint="CGPreflightScreenCaptureAccess 误报（launchd 缓存），OCR 最近 60 分钟内有 emit",
                )
            return CheckResult(
                ok=False,
                code="screen_capture_denied",
                hint=self._HINTS["screen_capture_denied"],
                action=_SCREEN_RECORDING_ACTION,
            )
        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="skipped")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        hint = self._HINTS.get(state.code, "屏幕录制权限异常")
        action = _SCREEN_RECORDING_ACTION if state.code == "screen_capture_denied" else None
        return Signal(level=self.level_when_failed, label=self.label_when_failed, hint=hint, action=action)
