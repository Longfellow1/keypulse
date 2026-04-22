import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from keypulse.store.db import get_conn
from keypulse.store.models import RawEvent, Session, SearchDoc, Policy, AppState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── RawEvent ─────────────────────────────────────────────────────────────────

def insert_raw_event(e: RawEvent) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO raw_events
           (source, event_type, ts_start, ts_end, app_name, window_title,
            process_name, content_text, content_hash, metadata_json,
            sensitivity_level, skipped_reason, session_id, speaker, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (e.source, e.event_type, e.ts_start, e.ts_end, e.app_name,
         e.window_title, e.process_name, e.content_text, e.content_hash,
         e.metadata_json, e.sensitivity_level, e.skipped_reason,
         e.session_id, e.speaker, e.created_at),
    )
    conn.commit()
    return cur.lastrowid


def query_raw_events(
    source: Optional[str] = None,
    app_name: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    conn = get_conn()
    clauses, params = [], []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if app_name:
        clauses.append("app_name LIKE ?")
        params.append(f"%{app_name}%")
    if since:
        clauses.append("ts_start >= ?")
        params.append(since)
    if until:
        clauses.append("ts_start <= ?")
        params.append(until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM raw_events {where} ORDER BY ts_start DESC LIMIT ?",
        (*params, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def purge_raw_events(since: Optional[str] = None, until: Optional[str] = None, app_name: Optional[str] = None):
    conn = get_conn()
    clauses, params = [], []
    if since:
        clauses.append("ts_start >= ?")
        params.append(since)
    if until:
        clauses.append("ts_start <= ?")
        params.append(until)
    if app_name:
        clauses.append("app_name LIKE ?")
        params.append(f"%{app_name}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn.execute(f"DELETE FROM raw_events {where}", params)
    conn.commit()


# ── Session ───────────────────────────────────────────────────────────────────

def upsert_session(s: Session):
    conn = get_conn()
    conn.execute(
        """INSERT INTO sessions
           (id, started_at, ended_at, app_name, primary_window_title,
            duration_sec, event_count, summary, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             ended_at=excluded.ended_at,
             duration_sec=excluded.duration_sec,
             event_count=excluded.event_count,
             summary=excluded.summary,
             updated_at=excluded.updated_at""",
        (s.id, s.started_at, s.ended_at, s.app_name, s.primary_window_title,
         s.duration_sec, s.event_count, s.summary, s.created_at, s.updated_at),
    )
    conn.commit()


def get_sessions(date_str: Optional[str] = None, limit: int = 200) -> list[dict]:
    """date_str format: YYYY-MM-DD"""
    conn = get_conn()
    if date_str:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE started_at LIKE ? ORDER BY started_at ASC LIMIT ?",
            (f"{date_str}%", limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_session_by_id(session_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


# ── SearchDoc ─────────────────────────────────────────────────────────────────

def insert_search_doc(doc: SearchDoc) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO search_docs (ref_type, ref_id, title, body, tags, app_name, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (doc.ref_type, doc.ref_id, doc.title, doc.body, doc.tags, doc.app_name, doc.created_at),
    )
    conn.commit()
    return cur.lastrowid


def search_docs_fts(query: str, app_name: Optional[str] = None, limit: int = 50) -> list[dict]:
    conn = get_conn()
    sql = """
        SELECT sd.*, bm25(search_docs_fts) as score
        FROM search_docs sd
        JOIN search_docs_fts ON search_docs_fts.rowid = sd.id
        WHERE search_docs_fts MATCH ?
    """
    params = [query]
    if app_name:
        sql += " AND sd.app_name LIKE ?"
        params.append(f"%{app_name}%")
    sql += " ORDER BY score, sd.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Policy ────────────────────────────────────────────────────────────────────

def get_all_policies() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM policies WHERE enabled=1 ORDER BY priority ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def insert_policy(p: Policy) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO policies (scope_type, scope_value, mode, enabled, priority, config_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (p.scope_type, p.scope_value, p.mode, int(p.enabled), p.priority,
         p.config_json, p.created_at, p.updated_at),
    )
    conn.commit()
    return cur.lastrowid


def seed_policies_from_config(policy_configs: list):
    """Insert policies from config if not already seeded."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    if count > 0:
        return  # already seeded
    for pc in policy_configs:
        p = Policy(
            scope_type=pc.scope_type,
            scope_value=pc.scope_value,
            mode=pc.mode,
            enabled=pc.enabled,
            priority=pc.priority,
        )
        insert_policy(p)


# ── AppState ──────────────────────────────────────────────────────────────────

def set_state(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO app_state(key, value, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now()),
    )
    conn.commit()


def get_state(key: str) -> Optional[str]:
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


# ── Retention ─────────────────────────────────────────────────────────────────

def apply_retention(retention_days: int):
    """Delete raw_events and clipboard docs older than retention_days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    conn = get_conn()
    deleted_raw_events = conn.execute(
        "DELETE FROM raw_events WHERE created_at < ?",
        (cutoff,),
    ).rowcount
    deleted_clipboard_docs = conn.execute(
        "DELETE FROM search_docs WHERE ref_type='clipboard' AND created_at < ?",
        (cutoff,),
    ).rowcount
    conn.commit()
    if deleted_raw_events or deleted_clipboard_docs:
        conn.execute("VACUUM")
