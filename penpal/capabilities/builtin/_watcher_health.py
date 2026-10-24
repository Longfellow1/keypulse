from __future__ import annotations

from dataclasses import dataclass

from penpal.capabilities.base import Capability, CheckResult, HealthState, Signal
from penpal.capabilities.builtin._common import now_ts
from penpal.runtime import evaluate


@dataclass(frozen=True)
class WatcherHealthSpec:
    watcher_name: str
    capability_name: str
    label_when_failed: str = "采集异常"
    hint: str = "采集线程异常，请运行 penpal doctor"


class WatcherHealthCapability(Capability):
    """Expose a watcher component from the shared daemon record."""

    level_when_failed = "warn"
    error_codes: set[str]

    def __init__(self, spec: WatcherHealthSpec) -> None:
        self._spec = spec
        self.name = spec.capability_name
        self.label_when_failed = spec.label_when_failed
        self.error_codes = {f"{spec.watcher_name}_watcher_gave_up"}

    def _probe(self) -> CheckResult:
        component = evaluate().components.get(self._spec.watcher_name)
        if component is None or component.state != "gave_up":
            return CheckResult(ok=True, code="ok")
        return CheckResult(ok=False, code=f"{self._spec.watcher_name}_watcher_gave_up", hint=self._spec.hint)

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(level=self.level_when_failed, label=self.label_when_failed, hint=state.detail or self._spec.hint, action=None)


WATCHER_HEALTH_SPECS: list[WatcherHealthSpec] = [
    WatcherHealthSpec("clipboard", "clipboard_watcher", hint="剪贴板采集线程异常，请运行 penpal doctor"),
    WatcherHealthSpec("browser", "browser_watcher", hint="浏览器标签采集线程异常，请运行 penpal doctor"),
    WatcherHealthSpec("browser_url", "browser_url_watcher", hint="浏览器 URL 采集线程异常，请检查自动化权限"),
    WatcherHealthSpec("keyboard_chunk", "keyboard_chunk_watcher", hint="键盘分块采集线程异常，请检查输入监控权限"),
    WatcherHealthSpec("window", "window_watcher", hint="窗口焦点采集线程异常，请运行 penpal doctor"),
    WatcherHealthSpec("ax_text", "ax_text_watcher", hint="AX 文本采集线程异常，请检查辅助功能权限"),
]
