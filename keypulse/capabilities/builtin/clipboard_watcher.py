from __future__ import annotations

import json

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts
from keypulse.store.repository import get_state


class ClipboardWatcherCapability(Capability):
    name = "clipboard_watcher"
    level_when_failed = "warn"
    label_when_failed = "采集异常"
    error_codes = {"clipboard_watcher_gave_up", "clipboard_heartbeat_gave_up"}

    _HINT = "剪贴板采集线程异常，请重启 daemon；若持续失败请重装应用"

    def _runtime_payload(self) -> dict:
        raw = get_state("capture_runtime") or ""
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _probe(self) -> CheckResult:
        runtime = self._runtime_payload()
        watchers = runtime.get("watchers")
        if not isinstance(watchers, dict):
            return CheckResult(ok=True, code="ok")

        health = watchers.get("clipboard")
        if not isinstance(health, dict):
            return CheckResult(ok=True, code="ok")

        if bool(health.get("gave_up")):
            return CheckResult(ok=False, code="clipboard_watcher_gave_up", hint=self._HINT)
        if bool(health.get("heartbeat_gave_up")):
            return CheckResult(ok=False, code="clipboard_heartbeat_gave_up", hint=self._HINT)
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
