from __future__ import annotations

import json
from dataclasses import dataclass

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts
from keypulse.store.repository import get_state


@dataclass(frozen=True)
class WatcherHealthSpec:
    """Per-watcher capability config.

    Each spec produces an independent Capability that reads `capture_runtime`
    (published by CaptureManager) and reports gave_up / heartbeat_gave_up
    failures. Adding a new watcher capability is one line in the registry.
    """
    watcher_name: str  # key under capture_runtime.watchers (e.g. "browser")
    capability_name: str  # registry id (e.g. "watcher_browser")
    label_when_failed: str = "采集异常"
    hint: str = "采集线程异常，请重启 daemon；若持续失败请重装应用"


def _runtime_payload() -> dict:
    raw = get_state("capture_runtime") or ""
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


class WatcherHealthCapability(Capability):
    """Surface a single watcher's health (gave_up / heartbeat_gave_up) into the
    capability layer. Generic — instantiate with a WatcherHealthSpec."""

    level_when_failed = "warn"
    error_codes: set[str]  # populated in __init__

    def __init__(self, spec: WatcherHealthSpec) -> None:
        self._spec = spec
        self.name = spec.capability_name
        self.label_when_failed = spec.label_when_failed
        self.error_codes = {
            f"{spec.watcher_name}_watcher_gave_up",
            f"{spec.watcher_name}_heartbeat_gave_up",
        }

    def _probe(self) -> CheckResult:
        runtime = _runtime_payload()
        watchers = runtime.get("watchers")
        if not isinstance(watchers, dict):
            return CheckResult(ok=True, code="ok")

        health = watchers.get(self._spec.watcher_name)
        if not isinstance(health, dict):
            # Watcher not present in runtime snapshot — could be disabled by
            # config (legitimate) or not yet started. Either way: don't fail.
            return CheckResult(ok=True, code="ok")

        if bool(health.get("gave_up")):
            return CheckResult(
                ok=False,
                code=f"{self._spec.watcher_name}_watcher_gave_up",
                hint=self._spec.hint,
            )
        if bool(health.get("heartbeat_gave_up")):
            return CheckResult(
                ok=False,
                code=f"{self._spec.watcher_name}_heartbeat_gave_up",
                hint=self._spec.hint,
            )
        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(
            level=self.level_when_failed,
            label=self.label_when_failed,
            hint=state.detail or self._spec.hint,
            action=None,
        )


# Single source of truth for watcher → capability mapping.
# To add a new watcher capability: add one entry here.
WATCHER_HEALTH_SPECS: list[WatcherHealthSpec] = [
    WatcherHealthSpec(
        watcher_name="clipboard",
        capability_name="clipboard_watcher",
        hint="剪贴板采集线程异常，请重启 daemon；若持续失败请重装应用",
    ),
    WatcherHealthSpec(
        watcher_name="browser",
        capability_name="browser_watcher",
        hint="浏览器标签采集线程异常，请重启 daemon",
    ),
    WatcherHealthSpec(
        watcher_name="window",
        capability_name="window_watcher",
        hint="窗口焦点采集线程异常，请重启 daemon",
    ),
    WatcherHealthSpec(
        watcher_name="ax_text",
        capability_name="ax_text_watcher",
        hint="AX 文本采集线程异常，请重启 daemon；可能与辅助功能权限相关",
    ),
    WatcherHealthSpec(
        watcher_name="ocr",
        capability_name="ocr_watcher",
        hint="OCR 采集线程异常，请重启 daemon",
    ),
]
