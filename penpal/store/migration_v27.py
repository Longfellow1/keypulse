"""Migration v27 (D28 follow-up): browser-history rows are bucketed by website, like browser_url.

chrome_history and safari_history rows carry their own URL in metadata_json["url"] (set by the sink from
the plugin's full_url). Up to v26 they were pinned to Personal, so every GitHub or Feishu visit and every
bank visit sat in Personal. This pass re-buckets them by URL with the same order as at ingest:
user rules (policies) > preset site table > Personal fallback.

  * Only rows whose bucket is still 'Personal' (the old default) are considered. A bucket the user chose
    by hand is never overwritten; a manual choice that happens to be Personal is indistinguishable and
    gets the site bucket (the same accepted trade-off as v26).
  * Like v21 / v23 / v26 this reads built-in defaults and stored user rules only, never user config
    (`penpal rules rebucket` applies config.toml later).
  * One keyset-paginated pass; run_migrations wraps it in a transaction together with the version stamp,
    and a rerun is a no-op (rows already moved off Personal are not selected again, and rows that stay
    Personal resolve to Personal again).
"""
from __future__ import annotations

import sqlite3

from penpal.store.buckets import SITE_HISTORY_SOURCES, load_bucket_rules, resolve_bucket, url_from_metadata

_BATCH = 2000
_OLD_DEFAULT = "Personal"


def migrate_v27(conn: sqlite3.Connection) -> None:
    try:
        rules = load_bucket_rules(conn)
    except sqlite3.OperationalError:  # legacy DB without a policies table: preset only
        rules = []
    sources = sorted(SITE_HISTORY_SOURCES)
    marks = ",".join("?" * len(sources))
    last = 0
    while True:
        rows = conn.execute(
            "SELECT id, source, app_name, process_name, metadata_json FROM raw_events "
            f"WHERE source IN ({marks}) AND bucket = ? AND id > ? ORDER BY id LIMIT ?",
            (*sources, _OLD_DEFAULT, last, _BATCH),
        ).fetchall()
        if not rows:
            return
        updates = []
        for rid, source, app, proc, meta in rows:
            new = resolve_bucket(
                source, app, proc, rules=rules, overrides={}, app_overrides={},
                url=url_from_metadata(meta), site_overrides={},
            )
            if new is not None and new != _OLD_DEFAULT:
                updates.append((new, rid))
        if updates:
            conn.executemany("UPDATE raw_events SET bucket = ? WHERE id = ?", updates)
        last = rows[-1][0]
