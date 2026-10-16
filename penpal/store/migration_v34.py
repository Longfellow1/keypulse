"""Migration v34: re-run the progressive-typing snapshot marking with the v34 rules (see penpal.store.snapshots).

v33 used the prefix and single-token pinyin rules only. v34 extends is_superseded_by with pinyin tails
that contain spaces and with in-place edits of the same draft. The marking is the same loop as v33
(migrate_v33 calls is_superseded_by at run time), so:

  * Only metadata is touched, and only by adding metadata_json.superseded_by. Nothing is deleted and
    rows already marked stay marked.
  * Chains collapse to the last row, because each adjacent pair is checked and the last row is never marked.
  * Idempotent: a second run writes the same values again and changes nothing.
"""
from __future__ import annotations

import logging
import sqlite3

from penpal.store.migration_v33 import migrate_v33

LOGGER = logging.getLogger(__name__)


def migrate_v34(conn: sqlite3.Connection) -> int:
    marked = migrate_v33(conn)
    LOGGER.info("migration v34 snapshot marking (v34 rules): %d superseded pairs", marked)
    return marked
