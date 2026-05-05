from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import iso_to_unix, now_ts, read_json


class HealthFreshnessCapability(Capability):
    name = "health_freshness"
    level_when_failed = "warn"
    label_when_failed = "体检失联"
    error_codes = {"health_stale", "health_missing"}

    _HINT = "健康监测未在运行，状态可能不准；请运行 make install 重挂体检"

    def __init__(self, health_path: Path | None = None, freshness_sec: int = 20 * 60):
        self._health_path = health_path or Path("~/.keypulse/health.json").expanduser()
        self._freshness_sec = freshness_sec

    def _probe(self) -> CheckResult:
        payload = read_json(self._health_path)
        if not isinstance(payload, dict):
            return CheckResult(ok=False, code="health_missing", hint=self._HINT)

        checked_at = iso_to_unix(payload.get("checked_at"))
        if checked_at is None:
            return CheckResult(ok=False, code="health_stale", hint=self._HINT)

        if time.time() - checked_at > self._freshness_sec:
            return CheckResult(ok=False, code="health_stale", hint=self._HINT)
        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(level=self.level_when_failed, label=self.label_when_failed, hint=self._HINT, action=None)
