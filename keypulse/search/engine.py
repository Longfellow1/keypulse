from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from keypulse.store.repository import search_docs_fts
from keypulse.search.query_builder import build_fts_query
from keypulse.store.db import get_conn


def _parse_since(since_str: Optional[str]) -> Optional[str]:
    """Parse --since value like '7d', '24h', '2026-04-01'."""
    if not since_str:
        return None
    since_str = since_str.strip()
    if since_str.endswith("d"):
        days = int(since_str[:-1])
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if since_str.endswith("h"):
        hours = int(since_str[:-1])
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    # Assume ISO date
    return since_str


def search(
    query: str,
    app_name: Optional[str] = None,
    since: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    Search search_docs via FTS5.
    Returns list of result dicts with: id, ref_type, ref_id, title, body, app_name, created_at, score
    """
    fts_query = build_fts_query(query)
    since_ts = _parse_since(since)

    conn = get_conn()
    params = [fts_query]
    extra_where = ""

    if app_name:
        extra_where += " AND sd.app_name LIKE ?"
        params.append(f"%{app_name}%")
    if since_ts:
        extra_where += " AND sd.created_at >= ?"
        params.append(since_ts)
    if source:
        extra_where += " AND sd.ref_type = ?"
        params.append(source)

    params.append(limit)

    sql = f"""
        SELECT sd.id, sd.ref_type, sd.ref_id, sd.title, sd.body, sd.app_name,
               sd.created_at, sd.tags, bm25(search_docs_fts) as score
        FROM search_docs sd
        JOIN search_docs_fts ON search_docs_fts.rowid = sd.id
        WHERE search_docs_fts MATCH ?{extra_where}
        ORDER BY score, sd.created_at DESC
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        # FTS5 syntax error or other — return empty
        return []


def recent_clipboard(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_docs WHERE ref_type='clipboard' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_manual(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_docs WHERE ref_type='manual' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_sessions_docs(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
