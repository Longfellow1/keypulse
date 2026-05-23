from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Iterable

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts
from keypulse.store.repository import get_state, set_state


_AUTOMATION_ACTION = "open://x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
_DENIED_BROWSERS_KEY = "browser_automation_denied_browsers"
_DENIED_FAILURES_KEY = "browser_automation_denied_failures"
_DENIED_DAILY_LIMIT = 3


def _normalize_browser_name(name: str) -> str:
    return " ".join(str(name or "").strip().split())


def _load_denied_browsers() -> set[str]:
    try:
        raw = (get_state(_DENIED_BROWSERS_KEY) or "").strip()
    except Exception:
        return set()
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except Exception:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {_normalize_browser_name(item) for item in parsed if _normalize_browser_name(item)}


def _save_denied_browsers(browsers: Iterable[str]) -> None:
    unique = sorted({_normalize_browser_name(item) for item in browsers if _normalize_browser_name(item)})
    try:
        set_state(_DENIED_BROWSERS_KEY, json.dumps(unique, ensure_ascii=False))
    except Exception:
        return


def _load_denied_failures() -> dict[str, dict[str, str | int]]:
    try:
        raw = (get_state(_DENIED_FAILURES_KEY) or "").strip()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}

    normalized: dict[str, dict[str, str | int]] = {}
    for browser_name, payload in parsed.items():
        name = _normalize_browser_name(browser_name)
        if not name or not isinstance(payload, dict):
            continue
        day = str(payload.get("day") or "").strip()
        try:
            count = int(payload.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        normalized[name] = {"day": day, "count": max(count, 0)}
    return normalized


def _save_denied_failures(failures: dict[str, dict[str, str | int]]) -> None:
    payload: dict[str, dict[str, str | int]] = {}
    for browser_name, info in sorted(failures.items()):
        name = _normalize_browser_name(browser_name)
        if not name or not isinstance(info, dict):
            continue
        day = str(info.get("day") or "").strip()
        try:
            count = int(info.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        payload[name] = {"day": day, "count": max(count, 0)}
    try:
        set_state(_DENIED_FAILURES_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception:
        return


def clear_browser_automation_denied_browsers_on_startup() -> list[str]:
    denied = sorted(_load_denied_browsers())
    if denied:
        _save_denied_browsers(())
    return denied


def mark_browser_automation_denied(browser_name: str, *, daily_limit: int = _DENIED_DAILY_LIMIT) -> bool:
    """Record one automation-denied probe.

    Returns True when this browser is now persisted in denied-list.
    Cool-down policy: only after N failures within the same local day.
    """
    normalized = _normalize_browser_name(browser_name)
    if not normalized:
        return False
    try:
        limit = max(int(daily_limit), 1)
    except (TypeError, ValueError):
        limit = _DENIED_DAILY_LIMIT

    denied = _load_denied_browsers()
    if normalized in denied:
        return True

    failures = _load_denied_failures()
    today = datetime.now().astimezone().date().isoformat()
    current = failures.get(normalized) or {}
    current_day = str(current.get("day") or "").strip()
    try:
        current_count = int(current.get("count") or 0)
    except (TypeError, ValueError):
        current_count = 0
    next_count = (current_count + 1) if current_day == today else 1
    failures[normalized] = {"day": today, "count": next_count}
    _save_denied_failures(failures)

    if next_count < limit:
        return False
    denied.add(normalized)
    _save_denied_browsers(denied)
    return True


def is_browser_automation_denied(stderr: str, returncode: int) -> bool:
    lowered = str(stderr or "").lower()
    if "not allowed" in lowered:
        return True
    if "not authorized" in lowered:
        return True
    if "apple events" in lowered:
        return True
    return returncode != 0


class BrowserAutomationCapability(Capability):
    name = "browser_automation"
    level_when_failed = "warn"
    label_when_failed = "采集建议"
    error_codes = {"browser_automation_denied"}

    _HINT = (
        "浏览器自动化权限未授权，请前往系统设置 → 隐私与安全性 → 自动化，"
        "勾选 KeyPulse 对 Safari/Google Chrome/Arc/Edge/Brave 的控制权限"
    )

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="skipped")

    def monitor(self) -> HealthState:
        denied = _load_denied_browsers()
        if denied:
            detail = f"{self._HINT}（已拒绝: {', '.join(sorted(denied))}）"
            return HealthState(
                ok=False,
                code="browser_automation_denied",
                last_checked=now_ts(),
                detail=detail,
            )

        script = 'tell application "Safari" to count of windows'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
        except Exception:
            return HealthState(ok=True, code="ok", last_checked=now_ts(), detail=None)

        stderr = (result.stderr or "").strip()
        if is_browser_automation_denied(stderr, result.returncode):
            mark_browser_automation_denied("Safari")
            return HealthState(
                ok=False,
                code="browser_automation_denied",
                last_checked=now_ts(),
                detail=self._HINT,
            )
        return HealthState(ok=True, code="ok", last_checked=now_ts(), detail=None)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        if state.code == "browser_automation_denied":
            return Signal(
                level="warn",
                label="采集建议",
                hint=state.detail or self._HINT,
                action=_AUTOMATION_ACTION,
            )
        return Signal(level="warn", label="采集建议", hint="浏览器自动化权限异常", action=None)
