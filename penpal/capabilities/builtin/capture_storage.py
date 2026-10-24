from __future__ import annotations

from penpal.capabilities.base import Capability, CheckResult, HealthState, Signal
from penpal.capabilities.builtin._common import now_ts
from penpal.runtime import evaluate

DISK_FULL_CODE = "capture_storage_disk_full"


class CaptureStorageCapability(Capability):
    """Surface capture's write state (CaptureManager pauses writes when the disk is full).

    Reads storage state from the shared daemon runtime record.
    """

    name = "capture_storage"
    level_when_failed = "err"
    label_when_failed = "采集写入失败"
    error_codes = {DISK_FULL_CODE}

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def _probe(self) -> CheckResult:
        storage = evaluate().components.get("storage")
        if storage is None or storage.state != "storage_error":
            return CheckResult(ok=True, code="ok")
        reason = storage.detail
        if reason == "disk_full":
            return CheckResult(
                ok=False,
                code=DISK_FULL_CODE,
                hint="磁盘已满，采集暂停写入；释放磁盘空间后会自动恢复",
            )
        return CheckResult(ok=False, code="capture_storage_error", hint="采集写入失败，请查看日志")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(
            level=self.level_when_failed,
            label=self.label_when_failed,
            hint=state.detail or "",
            action=None,
        )
