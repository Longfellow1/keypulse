from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.hud import health as hud_health


def test_read_health_returns_none_when_file_missing(tmp_path, monkeypatch):
    health_path = tmp_path / "health.json"
    monkeypatch.setattr(hud_health, "HEALTH_JSON_PATH", health_path)

    assert hud_health.read_health() is None


def test_read_health_returns_none_when_json_is_corrupt(tmp_path, monkeypatch):
    health_path = tmp_path / "health.json"
    health_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(hud_health, "HEALTH_JSON_PATH", health_path)

    assert hud_health.read_health() is None


def test_health_status_emoji_returns_red_for_missing_health():
    assert hud_health.health_status_emoji(None) == "🔴"


def test_health_status_emoji_returns_green_when_ok_and_fresh():
    now = datetime.now(timezone.utc)

    assert (
        hud_health.health_status_emoji(
            {
                "overall": "ok",
                "checked_at": now.isoformat(),
            }
        )
        == "🟢"
    )


def test_health_status_emoji_returns_red_when_ok_but_stale():
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)

    assert (
        hud_health.health_status_emoji(
            {
                "overall": "ok",
                "checked_at": stale.isoformat(),
            }
        )
        == "🔴"
    )


def test_health_status_emoji_returns_red_for_alert_overall():
    assert (
        hud_health.health_status_emoji(
            {
                "overall": "alert",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "alerts": ["daemon down"],
            }
        )
        == "🔴"
    )
