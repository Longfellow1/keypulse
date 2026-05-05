from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.health.report import HEALTH_JSON_PATH, read_health_report

_FRESHNESS_WINDOW = timedelta(minutes=20)


def read_health() -> dict | None:
    """Read the latest health.json payload.

    Missing, unreadable, or malformed files resolve to None.
    """
    payload = read_health_report(HEALTH_JSON_PATH)
    return payload or None


def _parse_checked_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def health_status_emoji(health: dict | None) -> str:
    """Return a two-state health indicator for the HUD title."""
    if not isinstance(health, dict):
        return "🔴"
    if str(health.get("overall") or "").lower() != "ok":
        return "🔴"
    checked_at = _parse_checked_at(health.get("checked_at"))
    if checked_at is None:
        return "🔴"
    if datetime.now(timezone.utc) - checked_at > _FRESHNESS_WINDOW:
        return "🔴"
    return "🟢"
