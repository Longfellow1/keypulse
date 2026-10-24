"""Delete-by-selector, shared by ``penpal purge`` and the HUD delete page (one core, two shells)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from penpal.store import repository


def purge_selector(
    *,
    today: bool = False,
    last_hours: Optional[int] = None,
    app: Optional[str] = None,
    source: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Selector kwargs for ``purge_raw_events``, or None when nothing was selected.

    The upper bound is fixed here, so events captured between a preview and the delete are not included.
    """
    now = now or datetime.now(timezone.utc)
    since = until = None
    if today:
        # "Today" is the user's local day; stored timestamps are UTC, so convert local midnight back to UTC.
        local_midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        since = local_midnight.astimezone(timezone.utc).isoformat()
        until = now.isoformat()
    elif last_hours is not None:
        since = (now - timedelta(hours=last_hours)).isoformat()
        until = now.isoformat()
    if since is None and app is None and source is None:
        return None
    return dict(since=since, until=until or now.isoformat(), app_name=app, source=source)


def run_purge(selector: dict, *, dry_run: bool = False) -> dict[str, int]:
    """Per-table row counts; ``dry_run`` counts and deletes nothing."""
    return repository.purge_raw_events(**selector, dry_run=dry_run)
