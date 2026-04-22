from __future__ import annotations
from typing import Optional

from keypulse.search.backends import resolve_search_backend
from keypulse.store.db import get_conn


def search(
    query: str,
    app_name: Optional[str] = None,
    since: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    backend = resolve_search_backend()
    return backend.search(query, app_name=app_name, since=since, source=source, limit=limit)


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
