"""Explicit "unavailable" state for source plugins (issue #22).

A source is either ``available`` (its original can be read now) or ``unavailable`` with one fixed reason
code. "Unavailable" is not a fault and is never an exception: callers (``sources list``, ``doctor``,
``health``, the MCP coverage note) show it as a status, and a read that cannot start simply yields no rows.

Reason codes (fixed enum, safe to show to an MCP client; no paths or messages):
- ``file_missing``: the history file / vault root / profile DB does not exist.
- ``permission_denied``: the file exists but cannot be read (permissions, or macOS privacy protection).
- ``db_locked``: the SQLite file reports a lock or busy state.
- ``unsupported_version``: the file is not a SQLite database, or lacks the tables this reader expects.
- ``not_found``: the plugin has nothing configured to look at (e.g. no approved vault).
- ``probe_error``: any other failure while probing; the reason is logged, not returned.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from penpal.utils.logging import get_logger

LOGGER = get_logger("sources.availability")

FILE_MISSING = "file_missing"
PERMISSION_DENIED = "permission_denied"
DB_LOCKED = "db_locked"
UNSUPPORTED_VERSION = "unsupported_version"
NOT_FOUND = "not_found"
PROBE_ERROR = "probe_error"

REASON_CODES = frozenset({FILE_MISSING, PERMISSION_DENIED, DB_LOCKED, UNSUPPORTED_VERSION, NOT_FOUND, PROBE_ERROR})


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str | None = None

    @property
    def status(self) -> str:
        return "available" if self.available else "unavailable"


AVAILABLE = Availability(available=True)


def unavailable(reason: str) -> Availability:
    if reason not in REASON_CODES:
        raise ValueError(f"unknown unavailable reason: {reason}")
    return Availability(available=False, reason=reason)


def check_file(path: Path) -> Availability:
    """Exists, is a regular file, and can be read."""
    try:
        if not path.is_file():
            return unavailable(FILE_MISSING)
        if not os.access(path, os.R_OK):
            return unavailable(PERMISSION_DENIED)
    except PermissionError:
        return unavailable(PERMISSION_DENIED)
    except OSError as exc:
        LOGGER.warning("availability probe failed for %s: %s", path.name, exc)
        return unavailable(PROBE_ERROR)
    return AVAILABLE


def check_sqlite(path: Path, *, required_tables: Iterable[str]) -> Availability:
    """File check, then a schema check on a lock-free read-only view.

    ``immutable=1`` skips SQLite locking, so a browser holding its History DB open does not look
    unavailable (the readers copy the file before querying, and that copy is readable too).
    """
    base = check_file(path)
    if not base.available:
        return base
    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return unavailable(DB_LOCKED)
        if "not a database" in text:
            return unavailable(UNSUPPORTED_VERSION)
        if "unable to open" in text:
            return unavailable(PERMISSION_DENIED)
        LOGGER.warning("sqlite availability probe failed for %s: %s", path.name, exc)
        return unavailable(PROBE_ERROR)
    except sqlite3.DatabaseError:
        return unavailable(UNSUPPORTED_VERSION)
    except OSError:
        return unavailable(PERMISSION_DENIED)
    if not set(required_tables) <= names:
        return unavailable(UNSUPPORTED_VERSION)
    return AVAILABLE
