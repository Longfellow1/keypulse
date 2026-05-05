from __future__ import annotations

from keypulse.capabilities.base import HealthState
from keypulse.hud import summary


def test_determine_service_status_reads_capabilities_first(monkeypatch) -> None:
    monkeypatch.setattr(
        summary,
        "load_capability_states",
        lambda: {
            "health_freshness": HealthState(
                ok=False,
                code="health_stale",
                last_checked=10.0,
                detail="stale",
            )
        },
    )

    level, label, hint, action = summary.determine_service_status(
        capture_status="running", health_ok=True
    )

    assert level == "warn"
    assert label == "体检失联"
    assert "健康监测" in hint
    assert action == ""


def test_determine_service_status_falls_back_to_legacy_codes_when_capabilities_missing(monkeypatch) -> None:
    monkeypatch.setattr(summary, "load_capability_states", lambda: {})
    monkeypatch.setattr(summary, "read_health", lambda: {"checked_at": "2026-05-04T00:00:00+00:00"})
    monkeypatch.setattr(
        summary,
        "get_state",
        lambda key: {
            "capture_error_code": "missing_appkit",
            "llm_error_code": "",
        }.get(key, ""),
    )

    level, label, hint, action = summary.determine_service_status(
        capture_status="running", health_ok=True
    )

    assert level == "err"
    assert label == "采集异常"
    assert "AppKit" in hint
    assert action == ""


def test_determine_service_status_surfaces_action_for_ax_denied(monkeypatch) -> None:
    monkeypatch.setattr(
        summary,
        "load_capability_states",
        lambda: {
            "accessibility_permission": HealthState(
                ok=False,
                code="ax_denied",
                last_checked=10.0,
                detail="denied",
            )
        },
    )

    level, label, hint, action = summary.determine_service_status(
        capture_status="running", health_ok=True
    )

    assert level == "err"
    assert label == "采集异常"
    assert "辅助功能" in hint
    assert action.startswith("open://")
    assert "Privacy_Accessibility" in action
