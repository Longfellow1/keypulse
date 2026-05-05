from __future__ import annotations

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts
from keypulse.store.repository import get_state


class PauseStateCapability(Capability):
    name = "pause_state"
    level_when_failed = "gray"
    label_when_failed = "已暂停"
    error_codes = {"paused"}

    def _probe(self) -> CheckResult:
        status = str(get_state("status") or "running").strip()
        if status == "paused":
            return CheckResult(ok=False, code="paused")
        return CheckResult(ok=True, code=status or "running")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts())

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(level="gray", label=self.label_when_failed, hint="", action=None)
