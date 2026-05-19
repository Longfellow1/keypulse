from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keypulse.app import _run_capability_self_check
from keypulse.capabilities.registry import get_default_registry
from keypulse.capabilities.store import load_states_raw
from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.store.db import get_conn
from keypulse.store.repository import get_state, set_state
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.dates import resolve_local_date
from keypulse.utils.paths import get_data_dir

_SELF_HEAL_RUNNING_KEY = "self_heal_running"
_SELF_HEAL_PROGRESS_KEY = "self_heal_progress"
_CAPABILITY_STATUS_PATH = Path("~/.keypulse/capability_status.json").expanduser()
_TOTAL_TIMEOUT_SEC = 8 * 60
_CORE_SOURCES = ("keyboard_chunk", "clipboard")
_DAEMON_LABEL = "com.keypulse.daemon"


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    message: str
    suggested_action: str = ""


def run_self_heal(*, dry_run: bool = False, source: str = "hud") -> dict[str, Any]:
    now = int(time.time())
    if str(get_state(_SELF_HEAL_RUNNING_KEY) or "").strip() == "1":
        return {"started": False, "status": "busy", "failed_step": ""}
    set_state(_SELF_HEAL_RUNNING_KEY, "1")
    deadline = time.monotonic() + _TOTAL_TIMEOUT_SEC
    steps: list[StepResult] = []
    try:
        _publish_progress("自愈开始：检查锁文件")
        steps.append(step_reconcile_locks(dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈进行中：刷新权限能力状态")
        steps.append(step_refresh_capabilities(dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈进行中：平滑重启后台服务")
        # 必须用 UTC，因为 raw_events.ts_start 存的是 UTC ISO8601（见 store/models.py _now）。
        # 之前 datetime.now() 是本地时间无 tz 字段，字符串比较时本地 19:xx > UTC 11:xx，导致 cutoff
        # 永远比所有 ts_start 大，step_wait_core_emit 5 分钟超时失败 → self_heal 反复 SIGTERM daemon。
        kickstart_ts = datetime.now(timezone.utc).isoformat()
        steps.append(step_restart_daemon(dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈进行中：等待后台就绪")
        steps.append(step_wait_daemon_ready(deadline=deadline, dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈进行中：等待核心数据恢复")
        steps.append(step_wait_core_emit(deadline=deadline, baseline_ts=kickstart_ts, dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈收尾：检查是否需要补跑今日日报")
        steps.append(step_rerun_daily_if_needed(deadline=deadline, dry_run=dry_run))
        _ensure_ok(steps[-1], deadline)

        _publish_progress("自愈完成：系统已恢复")
        return {
            "started": True,
            "status": "ok",
            "failed_step": "",
            "steps": [asdict(item) for item in steps],
            "source": source,
        }
    except RuntimeError as exc:
        failed_step = str(exc).split(":", 1)[0]
        _publish_progress("自愈失败，请查看提示后重试")
        return {
            "started": True,
            "status": "failed",
            "failed_step": failed_step,
            "reason": str(exc),
            "steps": [asdict(item) for item in steps],
            "source": source,
        }
    finally:
        set_state(_SELF_HEAL_RUNNING_KEY, "0")


def step_reconcile_locks(*, dry_run: bool) -> StepResult:
    data_dir = get_data_dir()
    removed = 0
    terminated = 0
    current_pid = os.getpid()
    for path in sorted(list(data_dir.glob("*.lock")) + list(data_dir.glob("*.pid"))):
        try:
            pid = _read_pid(path)
            if pid is None:
                if not dry_run:
                    path.unlink(missing_ok=True)
                removed += 1
                continue
            if pid == current_pid:
                continue
            if not _process_alive(pid):
                if not dry_run:
                    path.unlink(missing_ok=True)
                removed += 1
                continue
            if not dry_run:
                _terminate_process(pid, timeout_sec=5.0)
            terminated += 1
        except Exception:
            # Skip individual lock files we cannot access.
            continue
    return StepResult("step1_locks", True, f"清理完成：移除 {removed} 个，终止 {terminated} 个进程")


def step_refresh_capabilities(*, dry_run: bool) -> StepResult:
    if dry_run:
        return StepResult("step2_capabilities", True, "dry-run: 跳过权限刷新")
    registry = get_default_registry()
    _run_capability_self_check(registry)
    payload = load_states_raw()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(_CAPABILITY_STATUS_PATH, rendered, encoding="utf-8")
    return StepResult("step2_capabilities", True, "能力状态已刷新")


def step_restart_daemon(*, dry_run: bool) -> StepResult:
    if dry_run:
        return StepResult("step3_restart", True, "dry-run: 跳过 daemon 重启")
    target = f"gui/{os.getuid()}/{_DAEMON_LABEL}"
    result = subprocess.run(["launchctl", "kickstart", "-k", target], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return StepResult(
            "step3_restart",
            False,
            "daemon 重启失败",
            "请确认 KeyPulse 已在登录会话下安装并允许 launchd 管理",
        )
    return StepResult("step3_restart", True, "daemon 已发送重启指令")


def step_wait_daemon_ready(*, deadline: float, dry_run: bool) -> StepResult:
    if dry_run:
        return StepResult("step4_ready", True, "dry-run: 跳过就绪等待")
    timeout_deadline = min(deadline, time.monotonic() + 30.0)
    while time.monotonic() < timeout_deadline:
        pid = _launchctl_pid()
        if _process_alive(pid):
            return StepResult("step4_ready", True, f"daemon ready (pid={pid})")
        time.sleep(1.0)
    return StepResult(
        "step4_ready",
        False,
        "30 秒内 daemon 未就绪",
        "请到 System Settings → Privacy & Security 检查 KeyPulse 权限并重试",
    )


def step_wait_core_emit(*, deadline: float, baseline_ts: str, dry_run: bool) -> StepResult:
    if dry_run:
        return StepResult("step5_core_emit", True, "dry-run: 跳过核心数据验证")
    timeout_deadline = min(deadline, time.monotonic() + 5 * 60.0)
    while time.monotonic() < timeout_deadline:
        if _core_emit_count_since(baseline_ts) > 0:
            return StepResult("step5_core_emit", True, "核心数据源已恢复")
        time.sleep(2.0)
    return StepResult(
        "step5_core_emit",
        False,
        "核心数据源仍未恢复",
        "请到 System Settings → Privacy & Security → Accessibility / Input Monitoring 检查 KeyPulse 是否开启",
    )


def step_rerun_daily_if_needed(*, deadline: float, dry_run: bool) -> StepResult:
    if time.monotonic() > deadline:
        return StepResult("step6_daily", False, "超时，未执行日报补跑", "稍后手动执行 keypulse daily")
    now_local = datetime.now().astimezone()
    if (now_local.hour, now_local.minute) < (18, 30):
        return StepResult("step6_daily", True, "当前未到补跑日报窗口")
    today = resolve_local_date("today")
    payload = _today_run_record_payload(today)
    should_run = payload is None or _run_record_degraded(payload)
    if not should_run:
        return StepResult("step6_daily", True, "今日日报已就绪，无需补跑")
    if dry_run:
        return StepResult("step6_daily", True, "dry-run: 跳过日报补跑")
    run_daily(today, trigger="18:00")
    return StepResult("step6_daily", True, "今日日报补跑完成")


def _today_run_record_payload(date_str: str) -> dict[str, Any] | None:
    path = get_data_dir() / "run_records" / f"{date_str}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _run_record_degraded(payload: dict[str, Any]) -> bool:
    stage_status = payload.get("stage_status")
    if not isinstance(stage_status, dict):
        return False
    return any(str(value) != "ok" for value in stage_status.values())


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    return True


def _terminate_process(pid: int, *, timeout_sec: float) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.1)
    if _process_alive(pid):
        os.kill(pid, signal.SIGKILL)


def _launchctl_pid() -> int | None:
    try:
        result = subprocess.run(["launchctl", "list", _DAEMON_LABEL], check=False, capture_output=True, text=True)
    except Exception:
        return None
    text = "\n".join([result.stdout or "", result.stderr or ""])
    match = re.search(r'"?PID"?\s*=\s*(\d+)', text)
    if match:
        return int(match.group(1))
    for token in text.replace("=", " ").replace(";", " ").split():
        if token.isdigit() and int(token) > 1:
            return int(token)
    return None


def _core_emit_count_since(cutoff_iso: str) -> int:
    conn = get_conn()
    total = 0
    for source in _CORE_SOURCES:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM raw_events WHERE source=? AND ts_start>=?",
            (source, cutoff_iso),
        ).fetchone()
        total += int(row["c"] or 0) if row is not None else 0
    return total


def _publish_progress(message: str) -> None:
    payload = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": message}
    set_state(_SELF_HEAL_PROGRESS_KEY, json.dumps(payload, ensure_ascii=False))


def _ensure_ok(step: StepResult, deadline: float) -> None:
    if time.monotonic() > deadline:
        raise RuntimeError("timeout:自愈总超时 8 分钟")
    if step.ok:
        return
    raise RuntimeError(f"{step.name}:{step.message}:{step.suggested_action}")
