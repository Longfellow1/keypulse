from __future__ import annotations

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts


class AppKitRuntimeCapability(Capability):
    name = "appkit_runtime"
    level_when_failed = "err"
    label_when_failed = "采集异常"
    error_codes = {"missing_appkit"}

    _HINT = "Mac app 安装包缺少 AppKit 桥接，请重新打包 (make install)"

    def _probe(self) -> CheckResult:
        try:
            import AppKit  # noqa: F401
        except Exception:
            return CheckResult(ok=False, code="missing_appkit", hint=self._HINT)
        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="skipped")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        hint = self._HINT if state.code in self.error_codes else (state.detail or "采集组件异常，请重启 daemon")
        return Signal(level="err", label=self.label_when_failed, hint=hint, action=None)
