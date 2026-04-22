from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.utils.dates import local_day_bounds, resolve_local_date


def test_resolve_local_date_uses_local_day_for_today_and_yesterday():
    now = datetime(2026, 4, 19, 0, 30, tzinfo=timezone(timedelta(hours=8)))

    assert resolve_local_date("today", now=now) == "2026-04-19"
    assert resolve_local_date("yesterday", now=now) == "2026-04-18"
    assert resolve_local_date(None, yesterday=True, now=now) == "2026-04-18"


def test_local_day_bounds_convert_local_day_to_utc_range():
    tz = timezone(timedelta(hours=8))

    since, until = local_day_bounds("2026-04-19", tz=tz)

    assert since == "2026-04-18T16:00:00+00:00"
    assert until == "2026-04-19T15:59:59+00:00"
