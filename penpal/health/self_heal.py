"""Persistent restart policy used by the periodic healthcheck job."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any

from penpal.runtime import RuntimeState, RuntimeStatus, evaluate
from penpal.runtime.control import ControlResult, restart
from penpal.utils.atomic_io import atomic_write_text
from penpal.utils.paths import get_run_dir

CONSECUTIVE_FAILURE_THRESHOLD = 2
RESTART_COOLDOWN_SEC = 15 * 60
RESTART_BUDGET_PER_HOUR = 3
RESTART_BUDGET_WINDOW_SEC = 60 * 60
RESTART_STATES = frozenset({RuntimeState.NOT_RUNNING, RuntimeState.HUNG, RuntimeState.OUTDATED})


def _empty_state() -> dict[str, Any]:
    return {"consecutive_failures": 0, "cooldown_until": "", "restart_times": []}


def _read_state(path: Path) -> tuple[dict[str, Any], bool]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_state(), False
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_state(), True
    if not isinstance(raw, dict):
        return _empty_state(), True
    failures = raw.get("consecutive_failures")
    cooldown = raw.get("cooldown_until")
    restart_times = raw.get("restart_times")
    if not isinstance(failures, int) or failures < 0 or not isinstance(cooldown, str) or not isinstance(restart_times, list):
        return _empty_state(), True
    if any(not isinstance(item, str) for item in restart_times):
        return _empty_state(), True
    return {
        "consecutive_failures": failures,
        "cooldown_until": cooldown,
        "restart_times": restart_times,
    }, False


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _persist(path: Path, state: dict[str, Any]) -> None:
    rendered = json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n"
    atomic_write_text(path, rendered)


def run_watchdog(
    status: RuntimeStatus | None = None,
    *,
    now: datetime | None = None,
    restart_control: Callable[..., ControlResult] = restart,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Restart unhealthy managed runtime only after the persisted policy allows it."""
    checked = now or datetime.now(timezone.utc)
    checked = checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else checked.astimezone(timezone.utc)
    runtime_status = status or evaluate(now=checked)
    path = state_path or (get_run_dir() / "watchdog.json")
    state, corrupt_state = _read_state(path)
    cutoff = checked - timedelta(seconds=RESTART_BUDGET_WINDOW_SEC)
    restart_times = [
        value for raw in state["restart_times"]
        if (value := _parse_time(raw)) is not None and value > cutoff
    ]
    state["restart_times"] = [value.isoformat() for value in restart_times]

    # `penpal stop` boots the job out of launchd; only a job launchd still manages is ours to restart.
    unmanaged = not runtime_status.managed
    if runtime_status.state not in RESTART_STATES or unmanaged:
        state["consecutive_failures"] = 0
        _persist(path, state)
        return {
            "state": runtime_status.code,
            "action": "report_only",
            "reason": "not_managed_by_launchd" if unmanaged and runtime_status.state in RESTART_STATES else "runtime_healthy_or_paused",
            "consecutive_failures": 0,
            "restarts_in_window": len(restart_times),
            "budget_exhausted": False,
            "state_recovered": corrupt_state,
        }

    state["consecutive_failures"] += 1
    base = {
        "state": runtime_status.code,
        "consecutive_failures": state["consecutive_failures"],
        "restarts_in_window": len(restart_times),
        "state_recovered": corrupt_state,
    }
    if len(restart_times) >= RESTART_BUDGET_PER_HOUR:
        _persist(path, state)
        return {**base, "action": "report_only", "reason": "restart_budget_exhausted", "budget_exhausted": True}

    cooldown_until = _parse_time(state["cooldown_until"])
    if cooldown_until is not None and checked < cooldown_until:
        _persist(path, state)
        return {**base, "action": "report_only", "reason": "cooldown_active", "budget_exhausted": False}

    if state["consecutive_failures"] < CONSECUTIVE_FAILURE_THRESHOLD:
        _persist(path, state)
        return {**base, "action": "observe", "reason": "consecutive_failure_threshold", "budget_exhausted": False}

    result = restart_control("watchdog")
    restart_times.append(checked)
    state["restart_times"] = [value.isoformat() for value in restart_times]
    state["cooldown_until"] = (checked + timedelta(seconds=RESTART_COOLDOWN_SEC)).isoformat()
    if result.ok:
        state["consecutive_failures"] = 0
    _persist(path, state)
    return {
        **base,
        "action": "restart" if result.ok else "restart_failed",
        "reason": "control_completed" if result.ok else "control_failed",
        "budget_exhausted": False,
        "generation": result.generation,
        "error_zh": result.error_zh,
        "error_en": result.error_en,
        "restarts_in_window": len(restart_times),
    }
