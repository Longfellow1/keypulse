from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _system_iana_tz() -> str | None:
    """Resolve the system IANA timezone name from /etc/localtime symlink (macOS/Linux).

    datetime.now().astimezone().tzinfo returns a non-IANA tz on macOS (e.g. 'CST'),
    which breaks city lookup. /etc/localtime points at the canonical IANA file.
    """
    try:
        path = os.readlink("/etc/localtime")
    except OSError:
        return None
    marker = "/zoneinfo/"
    idx = path.find(marker)
    if idx < 0:
        return None
    return path[idx + len(marker):]


_IANA_TO_CITY: dict[str, str] = {
    "Asia/Shanghai": "上海",
    "Asia/Hong_Kong": "香港",
    "Asia/Taipei": "台北",
    "Asia/Tokyo": "东京",
    "Asia/Seoul": "首尔",
    "Asia/Singapore": "新加坡",
    "Asia/Bangkok": "曼谷",
    "Asia/Jakarta": "雅加达",
    "Asia/Kolkata": "孟买",
    "Asia/Dubai": "迪拜",
    "Europe/London": "伦敦",
    "Europe/Paris": "巴黎",
    "Europe/Berlin": "柏林",
    "Europe/Moscow": "莫斯科",
    "America/New_York": "纽约",
    "America/Chicago": "芝加哥",
    "America/Los_Angeles": "洛杉矶",
    "America/Toronto": "多伦多",
    "America/Vancouver": "温哥华",
    "America/Sao_Paulo": "圣保罗",
    "Australia/Sydney": "悉尼",
    "Pacific/Auckland": "奥克兰",
}


@lru_cache(maxsize=1)
def _configured_tz_name() -> str | None:
    """Read timezone from Config.load(). Cached; call clear_timezone_cache() in tests."""
    try:
        from keypulse.config import Config
        return Config.load().app.timezone
    except Exception:
        return None


def clear_timezone_cache() -> None:
    """Reset cached config timezone — for tests after monkeypatching config."""
    _configured_tz_name.cache_clear()


def local_timezone(now: datetime | None = None) -> tzinfo:
    name = _configured_tz_name() or _system_iana_tz()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    system_tz = (now or datetime.now()).astimezone().tzinfo
    return system_tz or timezone.utc


def current_tz_name() -> str:
    """IANA name of the active local timezone (e.g. 'Asia/Shanghai').

    Falls back to UTC offset string when the system tz has no IANA key.
    """
    tz = local_timezone()
    return getattr(tz, "key", None) or str(tz)


def local_city_label(tz_name: str | None = None) -> str:
    """Return the user-visible city label for an IANA tz.

    tz_name=None reads the current local timezone.
    Unknown IANA names fall back to the city segment ('Asia/Karachi' → 'Karachi').
    """
    if tz_name is None:
        tz_name = current_tz_name()
    if tz_name in _IANA_TO_CITY:
        return _IANA_TO_CITY[tz_name]
    if "/" in tz_name:
        return tz_name.split("/", 1)[1].replace("_", " ")
    return tz_name


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
