from __future__ import annotations

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import (
    capture_pipeline_healthy,
    now_ts,
    watcher_has_recent_emit,
)


_ACCESSIBILITY_ACTION = "open://x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


class AccessibilityPermissionCapability(Capability):
    name = "accessibility_permission"
    level_when_failed = "err"
    label_when_failed = "采集异常"
    error_codes = {"missing_ax", "ax_denied"}

    _HINTS = {
        "missing_ax": "Mac app 安装包缺少辅助功能桥接，请重新打包 (make install)",
        "ax_denied": "辅助功能权限未授权，请前往系统设置 → 隐私 → 辅助功能 → KeyPulse",
    }

    def _probe(self) -> CheckResult:
        try:
            import ApplicationServices
        except Exception:
            return CheckResult(ok=False, code="missing_ax", hint=self._HINTS["missing_ax"])

        try:
            trusted = bool(ApplicationServices.AXIsProcessTrusted())
        except Exception:
            trusted = False

        if not trusted:
            if capture_pipeline_healthy(["window", "ax_text"]) and watcher_has_recent_emit("ax_text", 1800.0):
                return CheckResult(
                    ok=True,
                    code="ok",
                    hint="AXIsProcessTrusted 误报（launchd 缓存），ax_text 最近 30 分钟内有 emit",
                )
            return CheckResult(
                ok=False,
                code="ax_denied",
                hint=self._HINTS["ax_denied"],
                action=_ACCESSIBILITY_ACTION,
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
        hint = self._HINTS.get(state.code, "辅助功能异常")
        action = _ACCESSIBILITY_ACTION if state.code == "ax_denied" else None
        return Signal(level="err", label=self.label_when_failed, hint=hint, action=action)
