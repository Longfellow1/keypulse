"""Migration v23 (D26): capture streams become queryable by granted hosts.

Three backfills over Penpal's own capture rows (raw_events.source in CAPTURE_SOURCES), all
idempotent and run inside the migration transaction:

  1. bucket    NULL buckets are classified by app: user bucket rules (policies, mode='bucket') >
               preset app table > Personal. Config overrides are not read (a migration must not depend
               on user config, same rule as v21); `penpal rules rebucket` re-applies them later.
               Only NULL buckets are touched, so a user's manual choice is never overwritten.
  2. secrets   content_text / window_title are re-run through redact_secrets (rows written before
               write-time redaction existed). The FTS index follows through the
               raw_events_fts_au trigger (it re-indexes on content_text / window_title updates).
  3. evidence  searchable capture events get a "penpal://event/<id>" evidence row (Penpal is the
               original record), so get_evidence works for them.

Rows are classified per distinct (app_name, process_name) pair and updated with executemany, so a
~25k row table finishes in about a second.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from penpal.foundation.desensitizer import redact_secrets
from penpal.store.app_categories import DEFAULT_APP_BUCKET
from penpal.store.buckets import CAPTURE_SOURCES, load_bucket_rules, resolve_bucket
from penpal.store.capture_events import CAPTURE_SEARCHABLE_EVENT_TYPES, PENPAL_LOCATOR_PREFIX

_BATCH = 2000


def _marks(items) -> str:
    return ",".join("?" * len(items))


def _backfill_buckets(conn: sqlite3.Connection) -> None:
    sources = sorted(CAPTURE_SOURCES)
    try:
        rules = load_bucket_rules(conn)
    except sqlite3.OperationalError:  # legacy DB without a policies table: preset only
        rules = []
    triples = conn.execute(
        "SELECT DISTINCT source, app_name, process_name FROM raw_events "
        f"WHERE bucket IS NULL AND source IN ({_marks(sources)})",
        sources,
    ).fetchall()
    for source, app, proc in triples:
        # Rules first (they carry the user's intent), then the preset table, then Personal.
        bucket = resolve_bucket(source, app, proc, rules=rules, overrides={}, app_overrides={})
        conn.execute(
            "UPDATE raw_events SET bucket = ? WHERE bucket IS NULL AND source = ? "
            "AND app_name IS ? AND process_name IS ?",
            (bucket or DEFAULT_APP_BUCKET, source, app, proc),
        )


def _backfill_redaction(conn: sqlite3.Connection) -> None:
    sources = sorted(CAPTURE_SOURCES)
    last = 0
    while True:
        rows = conn.execute(
            "SELECT id, content_text, window_title FROM raw_events "
            f"WHERE id > ? AND source IN ({_marks(sources)}) AND (content_text IS NOT NULL OR window_title IS NOT NULL) "
            "ORDER BY id LIMIT ?",
            (last, *sources, _BATCH),
        ).fetchall()
        if not rows:
            return
        changed = []
        for r in rows:
            text, title = redact_secrets(r["content_text"]), redact_secrets(r["window_title"])
            if text != r["content_text"] or title != r["window_title"]:
                changed.append((text, title, r["id"]))
        if changed:
            conn.executemany("UPDATE raw_events SET content_text = ?, window_title = ? WHERE id = ?", changed)
        last = rows[-1]["id"]


def _backfill_evidence(conn: sqlite3.Connection) -> None:
    types = list(CAPTURE_SEARCHABLE_EVENT_TYPES)
    conn.execute(
        "INSERT INTO evidence(event_id, locator, offset_or_line, content_hash, available, created_at) "
        f"SELECT e.id, ? || e.id, NULL, e.content_hash, 1, ? FROM raw_events e "
        f"WHERE e.event_type IN ({_marks(types)}) "
        "AND NOT EXISTS (SELECT 1 FROM evidence ev WHERE ev.event_id = e.id)",
        (PENPAL_LOCATOR_PREFIX, datetime.now(timezone.utc).isoformat(), *types),
    )


def migrate_v23(conn: sqlite3.Connection) -> None:
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        _backfill_buckets(conn)
        _backfill_redaction(conn)
        _backfill_evidence(conn)
    finally:
        conn.row_factory = previous_factory
