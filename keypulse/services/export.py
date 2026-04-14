from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from keypulse.store.repository import get_sessions, query_raw_events
from keypulse.store.db import get_conn


def _get_date_range(days: Optional[int] = None, date_str: Optional[str] = None):
    if date_str:
        since = f"{date_str}T00:00:00"
        until = f"{date_str}T23:59:59"
    elif days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        until = None
    else:
        since = None
        until = None
    return since, until


def export_json(days: Optional[int] = None, date_str: Optional[str] = None) -> str:
    since, until = _get_date_range(days, date_str)
    sessions = get_sessions(limit=10000) if not since else []
    if since:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM sessions WHERE started_at >= ? ORDER BY started_at",
            (since,),
        ).fetchall()
        sessions = [dict(r) for r in rows]
    events = query_raw_events(since=since, until=until, limit=50000)
    data = {"sessions": sessions, "events": events}
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def export_csv(days: Optional[int] = None, date_str: Optional[str] = None) -> str:
    since, until = _get_date_range(days, date_str)
    events = query_raw_events(since=since, until=until, limit=50000)
    if not events:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(events[0].keys()))
    writer.writeheader()
    writer.writerows(events)
    return buf.getvalue()


def export_markdown(days: Optional[int] = None, date_str: Optional[str] = None) -> str:
    since, until = _get_date_range(days, date_str)
    conn = get_conn()
    if since:
        sessions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM sessions WHERE started_at >= ? ORDER BY started_at",
                (since,),
            ).fetchall()
        ]
    else:
        sessions = get_sessions(limit=500)

    label = date_str or (f"Last {days} days" if days else "All time")
    lines = [f"# KeyPulse Export — {label}", ""]

    if not sessions:
        lines.append("_No data found._")
        return "\n".join(lines)

    lines.append("## Sessions")
    lines.append("")
    lines.append("| Start | End | App | Title | Duration |")
    lines.append("|-------|-----|-----|-------|----------|")
    for s in sessions:
        def fmt(ts):
            try:
                return datetime.fromisoformat(ts).astimezone().strftime("%H:%M")
            except Exception:
                return ts or ""
        dur = s.get("duration_sec") or 0
        h, m = dur // 3600, (dur % 3600) // 60
        dur_str = f"{h}h{m:02d}m" if h else f"{m}m"
        title = (s.get("primary_window_title") or "")[:50]
        lines.append(
            f"| {fmt(s['started_at'])} | {fmt(s['ended_at'])} | {s.get('app_name','')} | {title} | {dur_str} |"
        )

    return "\n".join(lines)
