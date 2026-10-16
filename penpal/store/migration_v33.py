"""Migration v33: mark progressive-typing snapshots as superseded (see penpal.store.snapshots).

For each SNAPSHOT_SOURCES row A (ordered by app, then ts_start), the NEXT row B of the same app is
checked: when B starts within 120 s after A and A is an earlier state of B, A gets
metadata_json.superseded_by = B.id. A chain A -> B -> C therefore leaves only C searchable.

  * Only metadata is touched (json_set keeps every other key). No row is deleted; the FTS index is
    unaffected because the trigger is on content_text / window_title only.
  * Rows with empty content are skipped (they can neither supersede nor be superseded).
  * Idempotent: the marking is a pure function of the stored rows, so a second run writes the same
    values again and changes nothing.
"""
from __future__ import annotations

import logging
import sqlite3

from penpal.store.snapshots import SNAPSHOT_SOURCES, is_superseded_by, mark_superseded, within_window

LOGGER = logging.getLogger(__name__)


def migrate_v33(conn: sqlite3.Connection) -> int:
    marks = ",".join("?" * len(SNAPSHOT_SOURCES))
    rows = conn.execute(
        "SELECT id, app_name, ts_start, content_text FROM raw_events "
        f"WHERE source IN ({marks}) AND content_text IS NOT NULL AND content_text <> '' "
        "ORDER BY app_name IS NOT NULL, app_name, ts_start, id",
        SNAPSHOT_SOURCES,
    ).fetchall()
    pairs: list[tuple[int, int]] = []
    previous = None  # (id, app_name, ts_start, text) of the previous row in the same app
    for row in rows:
        rid, app, ts, text = row
        if previous is not None and previous[1] == app:
            prev_id, _, prev_ts, prev_text = previous
            if within_window(prev_ts, ts) and is_superseded_by(prev_text, text):
                pairs.append((prev_id, rid))
        previous = row
    for prev_id, next_id in pairs:
        mark_superseded(conn, prev_id, next_id)
    LOGGER.info("migration v33 snapshot marking: %d superseded rows", len(pairs))
    return len(pairs)
