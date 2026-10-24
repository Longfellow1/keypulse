"""Read-only activity numbers for control surfaces (HUD overview / storage / delete pages).

Plain aggregates over ``raw_events``; no content leaves this module (only counts, names and timestamps).
"""
from __future__ import annotations

from penpal.store.db import get_conn


def source_activity(since_iso: str) -> dict[str, dict]:
    """source -> {"last": newest ts_start, "today": rows with ts_start >= since_iso, "total": rows}."""
    rows = get_conn().execute(
        "SELECT source, MAX(ts_start) AS last, SUM(ts_start >= ?) AS today, COUNT(*) AS total"
        " FROM raw_events GROUP BY source",
        (since_iso,),
    ).fetchall()
    return {r["source"]: {"last": r["last"], "today": int(r["today"] or 0), "total": int(r["total"])} for r in rows}


def top_apps(limit: int = 8) -> list[tuple[str, int]]:
    """Most frequent app names (exact stored spelling), for the delete-by-app choices."""
    rows = get_conn().execute(
        "SELECT app_name, COUNT(*) AS n FROM raw_events"
        " WHERE app_name IS NOT NULL AND app_name != '' GROUP BY app_name ORDER BY n DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [(r["app_name"], int(r["n"])) for r in rows]
