from __future__ import annotations

from keypulse.capabilities.base import HealthState, Signal
from keypulse.capabilities.registry import get_default_registry


def _state(*, ok: bool, code: str, last_checked: float) -> HealthState:
    return HealthState(ok=ok, code=code, last_checked=last_checked)


def test_aggregate_signal_prefers_highest_level() -> None:
    registry = get_default_registry()
    states = {
        "pause_state": _state(ok=False, code="paused", last_checked=100.0),
        "llm_backend": _state(ok=False, code="llm_timeout", last_checked=200.0),
        "accessibility_permission": _state(ok=False, code="ax_denied", last_checked=150.0),
    }

    signal = registry.aggregate_signal(states)

    assert signal.level == "err"
    assert signal.label == "采集异常"
    assert "辅助功能" in (signal.hint or "")


def test_aggregate_signal_prefers_recent_when_same_level() -> None:
    registry = get_default_registry()
    states = {
        "llm_backend": _state(ok=False, code="llm_no_key", last_checked=10.0),
        "health_freshness": _state(ok=False, code="health_stale", last_checked=20.0),
    }

    signal = registry.aggregate_signal(states)

    assert signal.level == "warn"
    assert signal.label == "体检失联"


def test_aggregate_signal_returns_ok_when_all_ok() -> None:
    registry = get_default_registry()
    states = {
        "llm_backend": _state(ok=True, code="ok", last_checked=10.0),
        "pause_state": _state(ok=True, code="running", last_checked=10.0),
    }

    signal = registry.aggregate_signal(states)

    assert signal == Signal(level="ok", label="正常", hint="", action=None)
