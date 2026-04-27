#!/usr/bin/env python3
"""One-time backfill scrub for historical raw_events.

Applies the cf11475 privacy rules to existing rows without touching the
daemon/CLI stack. Dry-run is the default; `--apply` writes changes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keypulse.capture.manager import (  # noqa: E402
    _TERMINAL_APPS,
    _browser_host_from_metadata,
    _host_matches_denylist,
    _is_terminal_app,
    _should_drop_browser_event,
)
from keypulse.config import Config  # noqa: E402
from keypulse.privacy.desensitizer import desensitize, desensitize_json_value  # noqa: E402
from keypulse.store.models import RawEvent  # noqa: E402


RULE1_BACKFILL_TAG = "terminal_app_backfill"
ROW_COLUMNS = (
    "id",
    "source",
    "event_type",
    "ts_start",
    "app_name",
    "process_name",
    "content_text",
    "metadata_json",
)


@dataclass
class RuleStats:
    matched: int = 0
    changed: int = 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill historical raw_events with cf11475 privacy rules."
    )
    parser.add_argument(
        "--db",
        default="~/.keypulse/keypulse.db",
        help="Path to the KeyPulse SQLite database (default: ~/.keypulse/keypulse.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes back to the database instead of dry-run mode.",
    )
    return parser.parse_args()


def _expand_path(path_value: str) -> Path:
    return Path(path_value).expanduser()


def _read_config() -> Config:
    return Config.load()


def _open_read_only_conn(db_path: Path) -> sqlite3.Connection:
    source_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(source_uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _open_write_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _backup_db(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(f"{db_path}.scrub-backup-{timestamp}")
    if backup_path.exists():
        raise FileExistsError(f"backup path already exists: {backup_path}")

    with _open_read_only_conn(db_path) as source_conn, sqlite3.connect(str(backup_path)) as backup_conn:
        source_conn.backup(backup_conn)

    return backup_path


def _load_rows(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {', '.join(ROW_COLUMNS)} FROM raw_events ORDER BY id"
    ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _current_rows(state: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [state[row_id] for row_id in sorted(state)]


def _row_event(row: dict[str, Any]) -> RawEvent:
    return RawEvent(
        source=row["source"],
        event_type=row["event_type"],
        ts_start=row["ts_start"],
        app_name=row["app_name"],
        process_name=row["process_name"],
        content_text=row["content_text"],
        metadata_json=row["metadata_json"],
        id=row["id"],
    )


def _is_terminal_history_row(app_name: Any, process_name: Any) -> bool:
    normalized_app = str(app_name or "").strip().lower()
    if normalized_app in _TERMINAL_APPS or _is_terminal_app(app_name):
        return True

    if str(app_name or "").strip() == "终端":
        return True

    process_lower = str(process_name or "").strip().lower()
    return any(hint in process_lower for hint in ("terminal", "iterm", "warp", "alacritty", "kitty", "ghostty"))


def _backfill_terminal_metadata(metadata_json: str | None) -> tuple[str, bool]:
    payload: Any = {}
    if metadata_json:
        try:
            payload = json.loads(metadata_json)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    updated = dict(payload)
    updated["text_dropped"] = RULE1_BACKFILL_TAG
    new_json = json.dumps(updated, ensure_ascii=False)
    return new_json, new_json != metadata_json


def _desensitize_text(text: str, cfg: Config) -> str:
    return desensitize(
        text,
        redact_emails=cfg.privacy.redact_emails,
        redact_phones=cfg.privacy.redact_phones,
        redact_tokens=cfg.privacy.redact_tokens,
    )


def _desensitize_metadata(metadata_json: str, cfg: Config) -> str | None:
    try:
        parsed = json.loads(metadata_json)
    except Exception:
        return None

    sanitized = desensitize_json_value(
        parsed,
        redact_emails=cfg.privacy.redact_emails,
        redact_phones=cfg.privacy.redact_phones,
        redact_tokens=cfg.privacy.redact_tokens,
    )
    try:
        return json.dumps(sanitized, ensure_ascii=False)
    except Exception:
        return None


def _rule_1_terminal_text(
    state: dict[int, dict[str, Any]],
    conn: sqlite3.Connection | None,
    apply: bool,
) -> RuleStats:
    stats = RuleStats()
    terminal_ids = [
        row["id"]
        for row in _current_rows(state)
        if row["source"] == "ax_text"
        and _is_terminal_history_row(row["app_name"], row["process_name"])
    ]
    stats.matched = len(terminal_ids)

    for row_id in terminal_ids:
        row = state[row_id]
        new_metadata_json, metadata_changed = _backfill_terminal_metadata(row["metadata_json"])
        content_changed = row["content_text"] is not None
        if not content_changed and not metadata_changed:
            continue

        if apply and conn is not None:
            conn.execute(
                "UPDATE raw_events SET content_text = ?, metadata_json = ? WHERE id = ?",
                (None, new_metadata_json, row_id),
            )

        row["content_text"] = None
        row["metadata_json"] = new_metadata_json
        stats.changed += 1

    return stats


def _rule_2_browser_deny(
    state: dict[int, dict[str, Any]],
    conn: sqlite3.Connection | None,
    deny_hosts: list[str],
    apply: bool,
) -> RuleStats:
    stats = RuleStats()
    delete_ids: list[int] = []

    for row in _current_rows(state):
        if row["source"] != "browser":
            continue
        host = _browser_host_from_metadata(row["metadata_json"])
        if host is None or not _host_matches_denylist(host, deny_hosts):
            continue
        event = _row_event(row)
        if not _should_drop_browser_event(event, deny_hosts):
            continue
        delete_ids.append(row["id"])
        stats.matched += 1

    for row_id in delete_ids:
        if apply and conn is not None:
            conn.execute("DELETE FROM raw_events WHERE id = ?", (row_id,))
        del state[row_id]
        stats.changed += 1

    return stats


def _rule_3_desensitize(
    state: dict[int, dict[str, Any]],
    conn: sqlite3.Connection | None,
    cfg: Config,
    apply: bool,
) -> RuleStats:
    stats = RuleStats()

    for row in _current_rows(state):
        content_text = row["content_text"]
        if content_text is None:
            continue
        stats.matched += 1
        new_text = _desensitize_text(content_text, cfg)
        if new_text == content_text:
            continue
        if apply and conn is not None:
            conn.execute(
                "UPDATE raw_events SET content_text = ? WHERE id = ?",
                (new_text, row["id"]),
            )
        row["content_text"] = new_text
        stats.changed += 1

    return stats


def _rule_4_metadata(
    state: dict[int, dict[str, Any]],
    conn: sqlite3.Connection | None,
    cfg: Config,
    apply: bool,
) -> RuleStats:
    stats = RuleStats()

    for row in _current_rows(state):
        metadata_json = row["metadata_json"]
        if metadata_json is None:
            continue
        stats.matched += 1
        new_metadata = _desensitize_metadata(metadata_json, cfg)
        if new_metadata is None or new_metadata == metadata_json:
            continue
        if apply and conn is not None:
            conn.execute(
                "UPDATE raw_events SET metadata_json = ? WHERE id = ?",
                (new_metadata, row["id"]),
            )
        row["metadata_json"] = new_metadata
        stats.changed += 1

    return stats


def _run_scrub(
    db_path: Path,
    *,
    apply: bool,
) -> tuple[Path, dict[str, RuleStats]]:
    backup_path = _backup_db(db_path)
    cfg = _read_config()

    if apply:
        conn = _open_write_conn(db_path)
    else:
        conn = _open_read_only_conn(db_path)

    try:
        state = _load_rows(conn)

        stats_1 = _rule_1_terminal_text(state, conn if apply else None, apply)
        stats_2 = _rule_2_browser_deny(
            state,
            conn if apply else None,
            cfg.privacy.url_deny_hosts,
            apply,
        )
        stats_3 = _rule_3_desensitize(state, conn if apply else None, cfg, apply)
        stats_4 = _rule_4_metadata(state, conn if apply else None, cfg, apply)

        if apply:
            conn.commit()

        return backup_path, {
            "rule_1_terminal_text": stats_1,
            "rule_2_browser_deny": stats_2,
            "rule_3_desensitize": stats_3,
            "rule_4_metadata": stats_4,
        }
    except Exception:
        if apply:
            conn.rollback()
        raise
    finally:
        conn.close()


def _print_stats(stats: dict[str, RuleStats]) -> None:
    print(
        f"rule_1_terminal_text: matched={stats['rule_1_terminal_text'].matched} "
        f"updated={stats['rule_1_terminal_text'].changed}"
    )
    print(
        f"rule_2_browser_deny: matched={stats['rule_2_browser_deny'].matched} "
        f"deleted={stats['rule_2_browser_deny'].changed}"
    )
    print(
        f"rule_3_desensitize: matched={stats['rule_3_desensitize'].matched} "
        f"changed={stats['rule_3_desensitize'].changed}"
    )
    print(
        f"rule_4_metadata: matched={stats['rule_4_metadata'].matched} "
        f"changed={stats['rule_4_metadata'].changed}"
    )


def main() -> int:
    args = _parse_args()
    db_path = _expand_path(args.db)
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    backup_path, stats = _run_scrub(db_path, apply=args.apply)

    print(f"backup: {backup_path}")
    print(f"mode: {'apply' if args.apply else 'dry-run'}")
    _print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
