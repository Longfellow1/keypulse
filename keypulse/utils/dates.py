from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo


def local_timezone(now: datetime | None = None) -> tzinfo:
    return (now or datetime.now().astimezone()).astimezone().tzinfo or timezone.utc


def resolve_local_date(date: str | None = None, *, yesterday: bool = False, now: datetime | None = None) -> str:
    tz = local_timezone(now)
    current = (now or datetime.now(tz)).astimezone(tz)
    if date == "today":
        return current.date().isoformat()
    if date == "yesterday" or yesterday or date is None:
        return (current.date() - timedelta(days=1)).isoformat()
    return date


def local_day_bounds(date_str: str, *, tz: tzinfo | None = None) -> tuple[str, str]:
    local_tz = tz or local_timezone()
    start_local = datetime.fromisoformat(f"{date_str}T00:00:00").replace(tzinfo=local_tz)
    end_local = start_local + timedelta(days=1) - timedelta(seconds=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )
