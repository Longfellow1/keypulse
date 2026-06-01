from __future__ import annotations

import json
from datetime import datetime, timedelta

from keypulse.capabilities.builtin import browser_automation


def _state_repo(monkeypatch):
    state: dict[str, str] = {}

    monkeypatch.setattr(browser_automation, "get_state", lambda key: state.get(key, ""))
    monkeypatch.setattr(browser_automation, "set_state", lambda key, value: state.__setitem__(key, value))
    return state


def test_mark_browser_automation_denied_uses_daily_cooldown(monkeypatch):
    state = _state_repo(monkeypatch)

    assert browser_automation.mark_browser_automation_denied("Safari") is False
    assert browser_automation.mark_browser_automation_denied("Safari") is False
    assert browser_automation.mark_browser_automation_denied("Safari") is True

    denied = json.loads(state[browser_automation._DENIED_BROWSERS_KEY])
    assert denied == ["Safari"]

    failures = json.loads(state[browser_automation._DENIED_FAILURES_KEY])
    assert failures["Safari"]["count"] == 3


def test_mark_browser_automation_denied_resets_count_on_new_day(monkeypatch):
    state = _state_repo(monkeypatch)

    yesterday = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
    state[browser_automation._DENIED_FAILURES_KEY] = json.dumps(
        {
            "Safari": {"day": yesterday, "count": 99},
        },
        ensure_ascii=False,
    )

    assert browser_automation.mark_browser_automation_denied("Safari") is False

    failures = json.loads(state[browser_automation._DENIED_FAILURES_KEY])
    assert failures["Safari"]["count"] == 1


def test_is_browser_automation_denied_only_on_explicit_permission_marker():
    # 真权限拒绝：stderr 含明确 marker
    assert browser_automation.is_browser_automation_denied(
        "execution error: Not allowed to send Apple events to Google Chrome. (-1743)", 1
    )
    assert browser_automation.is_browser_automation_denied(
        "execution error: Not authorized to send Apple events to Safari.", 1
    )
    assert browser_automation.is_browser_automation_denied(
        "Apple events permission required", 1
    )
    # 非权限错误：不能再误判为 denied（修复 silent bug 的核心）
    assert not browser_automation.is_browser_automation_denied(
        "Application isn't running.", 1
    )
    assert not browser_automation.is_browser_automation_denied(
        "Can't get front window of application", 1
    )
    assert not browser_automation.is_browser_automation_denied("", 1)
    # 正常成功
    assert not browser_automation.is_browser_automation_denied("", 0)


def test_clear_browser_automation_denied_browsers_on_startup_preserves_failures(monkeypatch):
    state = _state_repo(monkeypatch)

    state[browser_automation._DENIED_BROWSERS_KEY] = json.dumps(["Safari", "Google Chrome"], ensure_ascii=False)
    state[browser_automation._DENIED_FAILURES_KEY] = json.dumps(
        {
            "Safari": {"day": "2026-05-23", "count": 3},
        },
        ensure_ascii=False,
    )

    cleared = browser_automation.clear_browser_automation_denied_browsers_on_startup()

    assert cleared == ["Google Chrome", "Safari"]
    assert json.loads(state[browser_automation._DENIED_BROWSERS_KEY]) == []
    assert json.loads(state[browser_automation._DENIED_FAILURES_KEY])["Safari"]["count"] == 3
