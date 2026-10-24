from __future__ import annotations

from datetime import datetime, timezone

from penpal.health.product_delivery import evaluate_delivery_health
from penpal.store.db import init_db
from penpal.store.models import RawEvent
from penpal.store.repository import insert_raw_event
from penpal.runtime import RuntimeState, RuntimeStatus
from penpal.runtime.record import Component


def _seed_event(source: str, ts_start: str) -> None:
    insert_raw_event(
        RawEvent(
            source=source,
            event_type=f"{source}_event",
            ts_start=ts_start,
            speaker="user",
        )
    )


def _runtime_components(monkeypatch, components: dict[str, Component]) -> None:
    """Inject the same component shape consumed by the shared runtime evaluator."""
    import penpal.health.product_delivery as delivery

    status = RuntimeStatus(RuntimeState.RUNNING, components=components)
    monkeypatch.setattr(delivery, "evaluate", lambda: status)


def _healthy_components() -> dict[str, Component]:
    from penpal.observability.watcher_tiers import WATCHER_TIERS

    return {name: Component("running") for name in WATCHER_TIERS}


def test_delivery_health_silent_when_user_idle_but_watchers_healthy(tmp_path, monkeypatch):
    """Zero emits across all sources should NOT alert when watchers heartbeat OK."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".penpal" / "penpal.db")
    _runtime_components(monkeypatch, _healthy_components())

    result = evaluate_delivery_health(
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    watcher_alerts = [item for item in result.alerts if item.source.startswith("watcher:")]
    assert watcher_alerts == []


def test_delivery_health_flags_watcher_beat_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".penpal" / "penpal.db")
    _runtime_components(monkeypatch, {"keyboard_chunk": Component("gave_up", "heartbeat stalled")})

    result = evaluate_delivery_health(
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    alerts = [item for item in result.alerts if item.source == "watcher:keyboard_chunk"]
    assert len(alerts) == 1
    assert alerts[0].level == "critical"
    assert "heartbeat stalled" in alerts[0].message


def test_delivery_health_flags_watcher_last_error(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".penpal" / "penpal.db")
    _runtime_components(monkeypatch, {"clipboard": Component("gave_up", "permission denied")})

    result = evaluate_delivery_health(
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    alerts = [item for item in result.alerts if item.source == "watcher:clipboard"]
    assert len(alerts) == 1
    assert alerts[0].level == "critical"
    assert "permission denied" in alerts[0].message


def test_delivery_health_silent_for_unregistered_watcher(tmp_path, monkeypatch):
    """camera/browser_history/etc not in runtime payload should not alert."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".penpal" / "penpal.db")
    # Only seed core watchers — camera / browser_history / ax_text absent
    _runtime_components(monkeypatch, {name: Component("running") for name in ("keyboard_chunk", "clipboard", "browser_url", "window", "idle")})

    result = evaluate_delivery_health(
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    optional_alerts = [
        item for item in result.alerts
        if item.source in {"watcher:camera", "watcher:browser_history", "watcher:ax_text"}
    ]
    assert optional_alerts == []
