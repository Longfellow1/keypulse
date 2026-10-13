"""Migration v32: searchable context label for old imported rows (see keypulse.store.context_label).

Imported semantic_event rows from the git / codex / claude / browser-history / markdown plugins were
stored with an empty window_title. This pass fills it with the place the record lives (repo name,
project, site domain, note path), computed from the row's stored metadata_json.

  * Only semantic_event rows of CONTEXT_LABEL_SOURCES whose window_title is NULL or empty are touched,
    so live capture rows (real window titles) are never changed.
  * Rows whose label cannot be derived are left as they are.
  * The raw_events_fts_au trigger (AFTER UPDATE OF window_title) re-indexes each updated row, so the
    FTS index sees the label without a manual rebuild.
  * Idempotent: a second run finds no empty-title row with a derivable label and writes nothing.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from keypulse.store.context_label import CONTEXT_LABEL_SOURCES, context_label

LOGGER = logging.getLogger(__name__)
_BATCH = 2000
_SOURCES = tuple(sorted(CONTEXT_LABEL_SOURCES))


def _meta(raw: object) -> dict:
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def migrate_v32(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    marks = ",".join("?" * len(_SOURCES))
    counts: dict[str, list[int]] = {}
    last = 0
    while True:
        rows = conn.execute(
            "SELECT id, source, metadata_json FROM raw_events "
            f"WHERE event_type = 'semantic_event' AND source IN ({marks}) AND id > ? "
            "AND (window_title IS NULL OR window_title = '') ORDER BY id LIMIT ?",
            (*_SOURCES, last, _BATCH),
        ).fetchall()
        if not rows:
            break
        updates = []
        for rid, source, meta_json in rows:
            label = context_label(source, _meta(meta_json))
            bucket = counts.setdefault(source, [0, 0])
            if label is None:
                bucket[1] += 1
                continue
            updates.append((label, rid))
            bucket[0] += 1
        if updates:
            conn.executemany("UPDATE raw_events SET window_title = ? WHERE id = ?", updates)
        last = rows[-1][0]
    LOGGER.info("migration v32 context label backfill: %s",
                ", ".join(f"{s} {c[0]} labelled / {c[1]} without label" for s, c in sorted(counts.items())) or "none")
    return {s: (c[0], c[1]) for s, c in counts.items() if c[0] or c[1]}
