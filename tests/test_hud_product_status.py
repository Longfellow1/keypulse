from __future__ import annotations

from keypulse.health.alerts import render_alert
from keypulse.health.product_delivery import DeliveryHealth
from keypulse.hud import summary


def _delivery(alerts):
    return DeliveryHealth(
        alerts=alerts,
        run_record_ok=not any(item.source == "daily" for item in alerts),
        run_record_reason="",
        watcher_counts={},
        degraded=bool(alerts),
    )


def test_top_status_run_record_failure_is_red(monkeypatch):
    monkeypatch.setattr(summary, "get_state", lambda key: "")
    monkeypatch.setattr(
        summary,
        "evaluate_delivery_health",
        lambda **kwargs: _delivery(
            [
                render_alert(
                    level="critical",
                    source="daily",
                    message="今日日报未生成",
                    suggested_action="补跑日报",
                )
            ]
        ),
    )
    assert summary._choose_top_status(capture_status="running")[0] == "err"


def test_top_status_core_watcher_failure_is_red(monkeypatch):
    monkeypatch.setattr(summary, "get_state", lambda key: "")
    monkeypatch.setattr(
        summary,
        "evaluate_delivery_health",
        lambda **kwargs: _delivery(
            [
                render_alert(
                    level="critical",
                    source="watcher:keyboard_chunk",
                    message="核心数据源失活：keyboard 最近 1h 无新数据",
                    suggested_action="重启",
                )
            ]
        ),
    )
    assert summary._choose_top_status(capture_status="running")[0] == "err"


def test_top_status_standard_watcher_failure_is_yellow(monkeypatch):
    monkeypatch.setattr(summary, "get_state", lambda key: "")
    monkeypatch.setattr(
        summary,
        "evaluate_delivery_health",
        lambda **kwargs: _delivery(
            [
                render_alert(
                    level="warn",
                    source="watcher:window",
                    message="标准数据源失活：window 最近 1h 无新数据",
                    suggested_action="观察",
                )
            ]
        ),
    )
    assert summary._choose_top_status(capture_status="running")[0] == "warn"


def test_top_status_green_when_all_ok(monkeypatch):
    monkeypatch.setattr(summary, "get_state", lambda key: "")
    monkeypatch.setattr(summary, "evaluate_delivery_health", lambda **kwargs: _delivery([]))
    assert summary._choose_top_status(capture_status="running")[0] == "ok"
