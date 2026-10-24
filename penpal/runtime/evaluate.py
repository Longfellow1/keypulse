"""The single mapping from daemon evidence to user-visible runtime health."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import os
from typing import Callable

from penpal import __version__
from penpal.runtime.identity import installed_fingerprint as _installed_fingerprint
from penpal.runtime.launchd import is_installed, probe_daemon, probe_label, process_start_time
from penpal.runtime.record import Component, RuntimeRecord, read_record
from penpal.i18n import T

def _interval_from_env() -> float:
    try:
        return max(0.05, float(os.getenv("PENPAL_RUNTIME_RECORD_INTERVAL_SEC", "10")))
    except ValueError:
        return 10.0


RECORD_WRITE_INTERVAL_SEC = _interval_from_env()
HUNG_AFTER_SEC = RECORD_WRITE_INTERVAL_SEC * 3
STARTING_GRACE_SEC = RECORD_WRITE_INTERVAL_SEC * 3
WATCHER_PERMISSION_EVIDENCE_SEC = 30 * 60
CORE_LOOP_NAME = "core_loop"


class RuntimeState(str, Enum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    STARTING = "starting"
    RUNNING = "running"
    HUNG = "hung"
    OUTDATED = "outdated"
    PAUSED = "paused"


STATE_COPY: dict[RuntimeState, tuple[str, str, str, str, str]] = {
    RuntimeState.NOT_INSTALLED: ("尚未安装", "not installed", "后台服务尚未安装。", "The background service is not installed.", "penpal install init"),
    RuntimeState.NOT_RUNNING: ("未运行", "not running", "后台服务未运行。", "The background service is not running.", "penpal start"),
    RuntimeState.STARTING: ("正在启动", "starting", "后台服务正在启动。", "The background service is starting.", ""),
    RuntimeState.RUNNING: ("正在记录", "recording", "后台服务运行正常。", "The background service is running.", ""),
    RuntimeState.HUNG: ("后台无响应", "not responding", "后台服务核心循环没有推进。", "The daemon core loop has stopped making progress.", "penpal start"),
    RuntimeState.OUTDATED: ("后台代码已过期", "daemon code is outdated", "后台服务仍在运行旧版本代码。", "The daemon is running outdated code.", "penpal start"),
    RuntimeState.PAUSED: ("已暂停", "paused", "采集已暂停。", "Capture is paused.", "penpal resume"),
}
_FAILED_STATES = frozenset({
    RuntimeState.NOT_INSTALLED, RuntimeState.NOT_RUNNING, RuntimeState.HUNG, RuntimeState.OUTDATED,
})


@dataclass(frozen=True)
class Issue:
    component: str
    severity: str
    message_zh: str
    message_en: str
    fix_command: str = ""
    label_zh: str = ""
    label_en: str = ""


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    issues: tuple[Issue, ...] = ()
    components: dict[str, Component] = field(default_factory=dict)
    progress: dict[str, object] = field(default_factory=dict)
    pid: int | None = None
    generation: str | None = None
    managed: bool = False

    @property
    def code(self) -> str:
        return self.state.value

    @property
    def fix_command(self) -> str:
        return STATE_COPY[self.state][4]

    @property
    def failed(self) -> bool:
        return self.state in _FAILED_STATES


def state_text(state: RuntimeState | str, *, language: str | None = None) -> str:
    value = state if isinstance(state, RuntimeState) else RuntimeState(state)
    if language is None:
        return T(STATE_COPY[value][0], STATE_COPY[value][1])
    return STATE_COPY[value][0 if language == "zh" else 1]


def state_message(status: RuntimeStatus, *, language: str | None = None) -> str:
    issue = next((item for item in status.issues if item.component != "daemon"), None) if status.state == RuntimeState.RUNNING else None
    if issue is not None:
        message = issue.message_zh if language == "zh" else issue.message_en if language == "en" else ""
        if not message:
            from penpal.i18n import current_lang

            message = issue.message_zh if current_lang() == "zh" else issue.message_en
        fix = issue.fix_command
    else:
        message = state_text(status.state, language=language)
        fix = status.fix_command
    if fix:
        if language == "zh":
            return f"Penpal：{message} 请运行 `{fix}`。"
        if language == "en":
            return f"Penpal: {message}. Run `{fix}`."
        from penpal.i18n import T

        return T(f"Penpal：{message} 请运行 `{fix}`。", f"Penpal: {message}. Run `{fix}`.")
    if language == "zh":
        return f"Penpal：{message}。"
    if language == "en":
        return f"Penpal: {message}."
    from penpal.i18n import T

    return T(f"Penpal：{message}。", f"Penpal: {message}.")


def launchd_label_loaded(label: str) -> bool:
    """Expose read-only launchd state to install diagnostics without duplicating parsing."""
    return bool(probe_label(label).get("loaded"))


def watcher_recently_emitted(status: RuntimeStatus, name: str, now: datetime | None = None) -> bool:
    progress = status.progress.get(f"emit.{name}")
    last_done_at = getattr(progress, "last_done_at", None)
    age = _seconds_since(last_done_at, now or datetime.now(timezone.utc))
    return age is not None and age <= WATCHER_PERMISSION_EVIDENCE_SEC


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _seconds_since(value: str | None, now: datetime) -> float | None:
    then = _parse_time(value)
    return None if then is None else max(0.0, (now - then).total_seconds())


def _paused() -> bool:
    from penpal.capture.capture_control import is_capture_paused

    return is_capture_paused()


def _state_issue(state: RuntimeState) -> Issue | None:
    if state in (RuntimeState.RUNNING,):
        return None
    severity = "error" if state in _FAILED_STATES else "info"
    text = STATE_COPY[state]
    return Issue("daemon", severity, text[2], text[3], text[4], "后台服务", "Background service")


_COMPONENT_LABELS: dict[str, tuple[str, str]] = {
    "storage": ("采集存储", "Capture storage"),
    "permission.accessibility": ("辅助功能权限", "Accessibility permission"),
    "permission.input_monitoring": ("输入监控权限", "Input Monitoring permission"),
    "core_loop": ("后台核心循环", "Daemon core loop"),
}
_WATCHER_LABELS: dict[str, tuple[str, str]] = {
    "window": ("前台窗口", "Window watcher"),
    "clipboard": ("剪贴板", "Clipboard watcher"),
    "keyboard_chunk": ("键盘片段", "Keyboard watcher"),
    "ax_text": ("正文采集", "Accessibility text watcher"),
    "browser": ("浏览器", "Browser watcher"),
    "browser_url": ("浏览器地址", "Browser URL watcher"),
    "idle": ("空闲检测", "Idle watcher"),
}


def _component_issues(components: dict[str, Component]) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    for name, component in sorted(components.items()):
        label_zh, label_en = _COMPONENT_LABELS.get(name, _WATCHER_LABELS.get(name, (name, name)))
        if component.state == "missing_permission":
            issues.append(Issue(name, "warning", f"{label_zh}缺少系统权限。", f"{label_en} is missing a system permission.", "penpal doctor", label_zh, label_en))
        elif component.state == "storage_error":
            if component.detail == "disk_full":
                zh, en = "磁盘已满，采集暂停写入；释放空间后会自动恢复。", "Disk is full; capture will resume after space is freed."
            else:
                zh, en = "采集存储发生错误。", "Capture storage has an error."
            issues.append(Issue(name, "error", zh, en, "penpal doctor", label_zh, label_en))
        elif component.state == "gave_up":
            issues.append(Issue(name, "error", f"{label_zh}已停止重试。", f"{label_en} has exhausted its restart attempts.", "penpal doctor", label_zh, label_en))
    return tuple(issues)


def evaluate(
    now: datetime | None = None,
    *,
    record_reader: Callable[[], RuntimeRecord | None] = read_record,
    launchd_probe: Callable[[], dict] = probe_daemon,
    installed_fingerprint: Callable[[], str] = _installed_fingerprint,
    paused_reader: Callable[[], bool] = _paused,
) -> RuntimeStatus:
    """Evaluate installed code and daemon progress; idle event streams are healthy."""
    now = now or datetime.now(timezone.utc)
    record = record_reader()
    launchd = launchd_probe()
    loaded = bool(launchd.get("loaded"))
    launchd_pid = launchd.get("pid")

    if record is None:
        if not is_installed():
            state = RuntimeState.NOT_INSTALLED
        elif loaded and isinstance(launchd_pid, int):
            state = RuntimeState.STARTING
        else:
            state = RuntimeState.NOT_RUNNING
        issue = _state_issue(state)
        return RuntimeStatus(state, (issue,) if issue else (), pid=launchd_pid if isinstance(launchd_pid, int) else None, managed=loaded)

    if loaded:
        if not isinstance(launchd_pid, int):
            state = RuntimeState.STARTING if (_seconds_since(record.started_at, now) or 0) < STARTING_GRACE_SEC else RuntimeState.NOT_RUNNING
            issue = _state_issue(state)
            return RuntimeStatus(state, (issue,) if issue else (), pid=record.pid, generation=record.generation, managed=loaded)
        if launchd_pid != record.pid:
            state = RuntimeState.NOT_RUNNING
            issue = _state_issue(state)
            return RuntimeStatus(state, (issue,) if issue else (), pid=launchd_pid, generation=record.generation, managed=loaded)

    current_start = process_start_time(record.pid)
    if not current_start or current_start != record.process_start_time:
        state = RuntimeState.NOT_RUNNING
        issue = _state_issue(state)
        return RuntimeStatus(state, (issue,) if issue else (), pid=record.pid, generation=record.generation, managed=loaded)

    if record.code_fingerprint != installed_fingerprint() or record.version != __version__:
        state = RuntimeState.OUTDATED
    elif paused_reader():
        state = RuntimeState.PAUSED
    else:
        age = _seconds_since(record.progress.get(CORE_LOOP_NAME).last_done_at if CORE_LOOP_NAME in record.progress else None, now)
        startup_age = _seconds_since(record.started_at, now)
        if age is None or age > HUNG_AFTER_SEC:
            state = RuntimeState.STARTING if startup_age is not None and startup_age < STARTING_GRACE_SEC else RuntimeState.HUNG
        else:
            state = RuntimeState.RUNNING

    issues = tuple(filter(None, (_state_issue(state),))) + _component_issues(record.components)
    return RuntimeStatus(
        state, issues, record.components, record.progress, record.pid, record.generation, loaded,
    )
