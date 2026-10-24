"""Database cleanup operations for previously stored secret values."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from penpal.foundation.desensitizer import desensitize


@dataclass(frozen=True)
class ScrubChange:
    event_id: int
    before: str
    after: str


@dataclass(frozen=True)
class ScrubResult:
    changes: tuple[ScrubChange, ...]
    dry_run: bool
    applied: bool
    defaulted_to_dry_run: bool


class ScrubModeError(ValueError):
    """The requested scrub flags conflict."""


def scrub_secrets(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    apply: bool = False,
) -> ScrubResult:
    """Find sensitive raw event text and optionally persist its redacted replacement."""
    if apply and dry_run:
        raise ScrubModeError("Cannot use both --dry-run and --apply together.")
    defaulted_to_dry_run = not apply and not dry_run
    if defaulted_to_dry_run:
        dry_run = True

    rows = conn.execute(
        "SELECT id, content_text FROM raw_events WHERE content_text IS NOT NULL ORDER BY id"
    ).fetchall()
    changes = tuple(
        ScrubChange(int(row["id"]), row["content_text"], redacted)
        for row in rows
        if (redacted := desensitize(row["content_text"])) != row["content_text"]
    )

    if not dry_run and changes:
        conn.executemany(
            "UPDATE raw_events SET content_text=? WHERE id=?",
            [(change.after, change.event_id) for change in changes],
        )
        conn.commit()

    return ScrubResult(
        changes=changes,
        dry_run=dry_run,
        applied=apply,
        defaulted_to_dry_run=defaulted_to_dry_run,
    )
