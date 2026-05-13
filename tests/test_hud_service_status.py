from __future__ import annotations

from keypulse.capabilities.base import HealthState
from keypulse.hud import summary


def _ok_caps() -> dict[str, HealthState]:
    """All 13 capabilities reporting ok — runtime healthy baseline."""
    names = [
        "accessibility_permission",
        "appkit_runtime",
        "ax_text_watcher",
        "browser_watcher",
        "bundle_integrity",
        "clipboard_watcher",
        "health_freshness",
        "llm_backend",
        "obsidian_sync_freshness",
        "ocr_watcher",
        "pause_state",
        "screen_recording_permission",
        "window_watcher",
    ]
    return {n: HealthState(ok=True, code="ok", last_checked=10.0) for n in names}


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

    level, label, hint, action = summary.determine_service_status(capture_status="running")

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

    level, label, hint, action = summary.determine_service_status(capture_status="running")

    assert level == "err"
    assert label == "采集异常"
    assert "AppKit" in hint
    assert action == ""


def test_determine_service_status_surfaces_hint_action_for_ax_denied(monkeypatch) -> None:
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

    level, label, hint, action = summary.determine_service_status(capture_status="running")

    assert level == "warn"
    assert label == "采集建议"
    assert "辅助功能" in hint
    assert action.startswith("open://")
    assert "Privacy_Accessibility" in action


def test_determine_service_status_ignores_health_json_business_alerts(monkeypatch) -> None:
    """Regression: SYNC_STALE in health.json must not be reported as
    "健康监测未在运行" when all 13 capabilities (incl. health_freshness) are ok.

    Root cause this test guards: summary.py used to consult
    ``health.json.overall != ok`` as a fallback, conflating runtime health
    with business alerts. The health_freshness capability already owns the
    "healthcheck alive" judgement; health.json business alerts must not
    poison the HUD top-line status.
    """
    monkeypatch.setattr(summary, "load_capability_states", _ok_caps)
    monkeypatch.setattr(
        summary,
        "get_state",
        lambda key: {"capture_error_code": "", "llm_error_code": ""}.get(key, ""),
    )

    level, label, _hint, _action = summary.determine_service_status(capture_status="running")

    assert level == "ok"
    assert label == "都好"


def test_determine_service_status_returns_ok_when_caps_all_healthy(monkeypatch) -> None:
    """Sanity: 13 caps all ok + no legacy error codes → ok."""
    monkeypatch.setattr(summary, "load_capability_states", _ok_caps)
    monkeypatch.setattr(
        summary,
        "get_state",
        lambda key: {"capture_error_code": "", "llm_error_code": ""}.get(key, ""),
    )

    assert summary.determine_service_status(capture_status="running") == ("ok", "都好", "", "")


def test_determine_service_status_paused_short_circuits(monkeypatch) -> None:
    """Paused state wins over all capability signals."""
    monkeypatch.setattr(
        summary,
        "load_capability_states",
        lambda: {
            "ax_text_watcher": HealthState(ok=False, code="watcher_dead", last_checked=10.0)
        },
    )

    assert summary.determine_service_status(capture_status="paused") == ("gray", "已暂停", "", "")
