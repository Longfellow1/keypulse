from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from penpal.health import self_heal as watchdog
from penpal.runtime import RuntimeState, RuntimeStatus
from penpal.runtime.control import ControlResult


BASE = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _status(state: RuntimeState, *, managed: bool = True) -> RuntimeStatus:
    return RuntimeStatus(state, managed=managed)


def test_watchdog_never_restarts_a_daemon_the_user_stopped(tmp_path):
    """`penpal stop` boots the job out of launchd; the watchdog must not undo it."""
    calls = []
    state_path = tmp_path / "run" / "watchdog.json"
    for offset in range(5):
        result = watchdog.run_watchdog(
            _status(RuntimeState.NOT_RUNNING, managed=False), now=BASE + timedelta(minutes=offset),
            restart_control=lambda reason: calls.append(reason), state_path=state_path,
        )
        assert result["action"] == "report_only"
        assert result["reason"] == "not_managed_by_launchd"
    assert calls == []


def test_watchdog_waits_for_consecutive_failures_then_restarts(monkeypatch, tmp_path):
    calls = []

    def restart(reason):
        calls.append(reason)
        return ControlResult(True, "kickstart", "generation-new")

    state_path = tmp_path / "run" / "watchdog.json"
    first = watchdog.run_watchdog(
        _status(RuntimeState.HUNG), now=BASE, restart_control=restart, state_path=state_path,
    )
    second = watchdog.run_watchdog(
        _status(RuntimeState.HUNG), now=BASE + timedelta(seconds=1), restart_control=restart, state_path=state_path,
    )

    assert first["action"] == "observe"
    assert second["action"] == "restart"
    assert second["generation"] == "generation-new"
    assert calls == ["watchdog"]
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["consecutive_failures"] == 0
    assert len(persisted["restart_times"]) == 1


def test_watchdog_budget_is_persisted_and_resets_after_hour_window(tmp_path):
    calls = []

    def restart(reason):
        calls.append(reason)
        return ControlResult(True, "kickstart", f"generation-{len(calls)}")

    state_path = tmp_path / "run" / "watchdog.json"
    for index in range(watchdog.RESTART_BUDGET_PER_HOUR):
        at = BASE + timedelta(minutes=16 * index)
        watchdog.run_watchdog(_status(RuntimeState.NOT_RUNNING), now=at, restart_control=restart, state_path=state_path)
        restarted = watchdog.run_watchdog(
            _status(RuntimeState.NOT_RUNNING), now=at + timedelta(seconds=1),
            restart_control=restart, state_path=state_path,
        )
        assert restarted["action"] == "restart"

    exhausted = watchdog.run_watchdog(
        _status(RuntimeState.HUNG), now=BASE + timedelta(minutes=48),
        restart_control=restart, state_path=state_path,
    )
    assert exhausted["action"] == "report_only"
    assert exhausted["reason"] == "restart_budget_exhausted"
    assert exhausted["budget_exhausted"] is True
    assert len(calls) == watchdog.RESTART_BUDGET_PER_HOUR

    after_window = BASE + timedelta(minutes=61)
    resumed = watchdog.run_watchdog(
        _status(RuntimeState.HUNG), now=after_window,
        restart_control=restart, state_path=state_path,
    )
    assert resumed["action"] == "restart"
    assert len(calls) == watchdog.RESTART_BUDGET_PER_HOUR + 1


def test_watchdog_cooldown_suppresses_restarts_and_healthy_state_resets_streak(tmp_path):
    calls = []
    state_path = tmp_path / "run" / "watchdog.json"

    def restart(reason):
        calls.append(reason)
        return ControlResult(False, "kickstart", error_zh="失敗", error_en="failed")

    watchdog.run_watchdog(_status(RuntimeState.OUTDATED), now=BASE, restart_control=restart, state_path=state_path)
    attempted = watchdog.run_watchdog(
        _status(RuntimeState.OUTDATED), now=BASE + timedelta(seconds=1),
        restart_control=restart, state_path=state_path,
    )
    cooled = watchdog.run_watchdog(
        _status(RuntimeState.OUTDATED), now=BASE + timedelta(minutes=1),
        restart_control=restart, state_path=state_path,
    )
    healthy = watchdog.run_watchdog(
        _status(RuntimeState.RUNNING), now=BASE + timedelta(minutes=2),
        restart_control=restart, state_path=state_path,
    )

    assert attempted["action"] == "restart_failed"
    assert cooled["reason"] == "cooldown_active"
    assert healthy["consecutive_failures"] == 0
    assert calls == ["watchdog"]
