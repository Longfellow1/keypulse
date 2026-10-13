"""Migration v26 (P0-12c, issue #53, D27 / D28): fixes found by sampling real data.

One transactional, idempotent pass (run_migrations takes a .bak first). Order matters:

  1. invisible    zero-width / bidi / BOM / soft-hyphen characters are stripped from content_text and
                  window_title (all sources; clipboard search_docs follow). FTS follows via the update trigger.
  2. overlay      window rows of a system overlay app (Setup Assistant, loginwindow, ...) and their
                  sessions are deleted; other capture rows of such an app lose app_name / process_name
                  (content kept) and, if still on the default bucket, become Personal.
  3. noise        claude_code / codex_cli rows run through the shared noise predicate: whole-noise rows
                  are deleted, inline <system-reminder> blocks are cut out (evidence hash follows).
  4. placeholder  ax_text rows that are an input placeholder / UI prompt are deleted.
  5. zsh          zsh_history rows were decoded without unmetafying and cannot be repaired (the original
                  bytes are gone), so they are deleted and any zsh sync cursor is reset. The plugin
                  re-reads the whole history file on its next sync (no cursor = full read) and its dedup
                  keys are stable, so nothing is duplicated.
  6. site         browser rows still on their old app-level bucket are re-bucketed by website (D28). A
                  row whose bucket differs from what the old rules would give was chosen by the user and
                  is left alone.

Deleting a raw_events row removes its FTS entry and evidence row through the existing triggers. Like
v21 / v23 / v24 this reads built-in defaults and stored user bucket rules only, never user config
(`keypulse rules rebucket` applies config.toml later).
"""
from __future__ import annotations

import hashlib
import sqlite3

from keypulse.capture.hygiene import is_placeholder_text, is_system_overlay
from keypulse.store.buckets import (
    CAPTURE_SOURCES,
    BrowserUrlTimeline,
    SITE_INHERIT_SOURCES,
    SITE_OWN_URL_SOURCES,
    load_bucket_rules,
    resolve_bucket,
    url_from_metadata,
    wants_site_url,
)
from keypulse.utils.text_filters import INVISIBLE_CHARS_RE, strip_invisible

_BATCH = 2000
_NOISE_SOURCES = ("claude_code", "codex_cli")
_PLACEHOLDER_MAX_LEN = 80


def _marks(items) -> str:
    return ",".join("?" * len(items))


def _has_search_docs(conn: sqlite3.Connection) -> bool:
    # A very old legacy DB may reach this step without the table.
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'search_docs'").fetchone() is not None


def _delete_rows(conn: sqlite3.Connection, ids: list[int]) -> None:
    rows = [(i,) for i in ids]
    conn.executemany("DELETE FROM raw_events WHERE id = ?", rows)
    if _has_search_docs(conn):
        conn.executemany(
            "DELETE FROM search_docs WHERE ref_type IN ('clipboard','manual') AND ref_id = CAST(? AS TEXT)", rows
        )


def _batches(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    """Keyset pagination over raw_events (sql selects `id` first and ends with `id > ? ORDER BY id LIMIT ?`)."""
    last = 0
    while True:
        rows = conn.execute(sql, (*params, last, _BATCH)).fetchall()
        if not rows:
            return
        yield rows
        last = rows[-1][0]


def _strip_invisible_chars(conn: sqlite3.Connection) -> int:
    changed = 0
    for rows in _batches(
        conn,
        "SELECT id, content_text, window_title FROM raw_events "
        "WHERE (content_text IS NOT NULL OR window_title IS NOT NULL) AND id > ? ORDER BY id LIMIT ?",
    ):
        updates = []
        for rid, text, title in rows:
            if (text and INVISIBLE_CHARS_RE.search(text)) or (title and INVISIBLE_CHARS_RE.search(title)):
                updates.append((strip_invisible(text), strip_invisible(title), rid))
        if updates:
            conn.executemany("UPDATE raw_events SET content_text = ?, window_title = ? WHERE id = ?", updates)
            changed += len(updates)
    # clipboard / manual copies are mirrored into search_docs
    docs = []
    for doc_id, title, body in (conn.execute(
        "SELECT id, title, body FROM search_docs WHERE ref_type IN ('clipboard','manual')"
    ).fetchall() if _has_search_docs(conn) else []):
        if (title and INVISIBLE_CHARS_RE.search(title)) or (body and INVISIBLE_CHARS_RE.search(body)):
            docs.append((strip_invisible(title), strip_invisible(body), doc_id))
    if docs:
        conn.executemany("UPDATE search_docs SET title = ?, body = ? WHERE id = ?", docs)
    return changed


def _scrub_overlay_apps(conn: sqlite3.Connection) -> None:
    pairs = conn.execute("SELECT DISTINCT source, app_name, process_name FROM raw_events").fetchall()
    for source, app, proc in pairs:
        if source not in CAPTURE_SOURCES or not (app or proc) or not is_system_overlay(app, proc):
            continue
        if source == "window":
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM raw_events WHERE source = 'window' AND app_name IS ? AND process_name IS ?", (app, proc)
            )]
            _delete_rows(conn, ids)
            continue
        conn.execute(
            "UPDATE raw_events SET app_name = NULL, process_name = NULL, "
            "bucket = CASE WHEN bucket IS NULL OR bucket = 'Personal' THEN 'Personal' ELSE bucket END "
            "WHERE source = ? AND app_name IS ? AND process_name IS ?",
            (source, app, proc),
        )
        if _has_search_docs(conn):
            conn.execute(
                "UPDATE search_docs SET app_name = NULL WHERE ref_type IN ('clipboard','manual') AND app_name IS ?", (app,)
            )
    try:
        names = [r[0] for r in conn.execute("SELECT DISTINCT app_name FROM sessions WHERE app_name IS NOT NULL")]
    except sqlite3.OperationalError:
        return
    for name in names:
        if is_system_overlay(name):
            conn.execute("DELETE FROM sessions WHERE app_name = ?", (name,))


def _scrub_ai_transcript_noise(conn: sqlite3.Connection) -> None:
    # Imported here: keypulse.sources pulls in the registry, which imports the store at module load.
    from keypulse.sources.cleaning.noise import clean_message_part

    for rows in _batches(
        conn,
        "SELECT id, content_text FROM raw_events "
        f"WHERE source IN ({_marks(_NOISE_SOURCES)}) AND content_text IS NOT NULL AND id > ? ORDER BY id LIMIT ?",
        _NOISE_SOURCES,
    ):
        deletes: list[int] = []
        updates: list[tuple] = []
        hashes: list[tuple] = []
        for rid, text in rows:
            cleaned = clean_message_part(text)
            if cleaned == text.strip():
                continue
            if not cleaned:
                deletes.append(rid)
            else:
                updates.append((cleaned, rid))
                hashes.append((hashlib.sha256(cleaned.encode("utf-8")).hexdigest(), rid))
        if updates:
            conn.executemany("UPDATE raw_events SET content_text = ? WHERE id = ?", updates)
            conn.executemany("UPDATE evidence SET content_hash = ? WHERE event_id = ?", hashes)
        if deletes:
            _delete_rows(conn, deletes)


def _scrub_placeholders(conn: sqlite3.Connection) -> None:
    for rows in _batches(
        conn,
        "SELECT id, content_text FROM raw_events WHERE source = 'ax_text' AND content_text IS NOT NULL "
        f"AND length(content_text) <= {_PLACEHOLDER_MAX_LEN} AND id > ? ORDER BY id LIMIT ?",
    ):
        ids = [rid for rid, text in rows if is_placeholder_text(text)]
        if ids:
            _delete_rows(conn, ids)


def _reset_zsh(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM raw_events WHERE source = 'zsh_history'")
    conn.execute("DELETE FROM sync_cursors WHERE source_id IN (SELECT id FROM sources WHERE type = 'zsh_history')")


def _rebucket_by_site(conn: sqlite3.Connection) -> None:
    try:
        rules = load_bucket_rules(conn)
    except sqlite3.OperationalError:  # legacy DB without a policies table: preset only
        rules = []
    timeline = BrowserUrlTimeline(conn)
    sources = sorted(SITE_OWN_URL_SOURCES | SITE_INHERIT_SOURCES)
    old_cache: dict[tuple, str | None] = {}
    for rows in _batches(
        conn,
        "SELECT id, source, app_name, process_name, bucket, ts_start, metadata_json, window_title FROM raw_events "
        f"WHERE source IN ({_marks(sources)}) AND id > ? ORDER BY id LIMIT ?",
        tuple(sources),
    ):
        updates = []
        for rid, source, app, proc, bucket, ts, meta, title in rows:
            if not wants_site_url(source, app, proc):
                continue
            key = (source, app, proc)
            if key not in old_cache:
                old_cache[key] = resolve_bucket(source, app, proc, rules=rules, overrides={}, app_overrides={}, site_overrides={})
            if bucket is not None and bucket != old_cache[key]:
                continue  # not on the old app-level bucket: the user's choice
            url = url_from_metadata(meta)
            if url is None and source in SITE_INHERIT_SOURCES:
                url = timeline.lookup(app, proc, ts, title)
            new = resolve_bucket(source, app, proc, rules=rules, overrides={}, app_overrides={}, url=url, site_overrides={})
            if new is not None and new != bucket:
                updates.append((new, rid))
        if updates:
            conn.executemany("UPDATE raw_events SET bucket = ? WHERE id = ?", updates)


def migrate_v26(conn: sqlite3.Connection) -> None:
    _strip_invisible_chars(conn)
    _scrub_overlay_apps(conn)
    _scrub_ai_transcript_noise(conn)
    _scrub_placeholders(conn)
    _reset_zsh(conn)
    _rebucket_by_site(conn)
