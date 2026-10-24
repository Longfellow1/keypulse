"""Migration v24 (D27): run existing capture rows through the write-time hygiene gate.

Rows the gate would have dropped are deleted (the raw_events delete trigger removes their FTS entry and
evidence row; clipboard search_docs rows are removed too). Rows the gate would have reduced to
metadata only keep app + time, with content_text / content_hash (and the title for window and
browser_url rows) nulled; the update trigger re-indexes FTS. Excluded apps (default bundle ids,
patterns and app names) and denied browser hosts are removed as well.

Runs in the migration transaction (all or nothing); run_migrations takes a .bak first. Idempotent:
a second pass finds nothing left to change. It reads built-in defaults only, never user config
(same rule as v21 / v23).
"""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit

from penpal.foundation.capture_hygiene import (
    DROP,
    METADATA_ONLY,
    capture_gate,
    is_blacklisted_app,
    strip_url_query,
)
from penpal.config import PrivacyConfig
from penpal.store.buckets import CAPTURE_SOURCES

_BATCH = 2000
_GATED_SOURCES = ("keyboard_chunk", "window", "clipboard", "ax_text", "browser_url")


def _marks(items) -> str:
    return ",".join("?" * len(items))


def _denied_host(metadata_json: str | None, deny_hosts: list[str]) -> bool:
    if not metadata_json:
        return False
    try:
        url = json.loads(metadata_json).get("url")
        host = (urlsplit(str(url)).hostname or "").lower() if url else ""
    except Exception:
        return False
    return bool(host) and any(host == d or host.endswith("." + d) for d in (x.lower() for x in deny_hosts))


def _row_url(metadata_json: str | None) -> str | None:
    try:
        url = json.loads(metadata_json).get("url") if metadata_json else None
    except Exception:
        return None
    return str(url) if url else None


def _strip_urls(metadata_json: str | None, reason: str) -> str:
    try:
        payload = json.loads(metadata_json) if metadata_json else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("url"):
        payload["url"] = strip_url_query(str(payload["url"]))
    entities = payload.get("entities")
    if isinstance(entities, dict) and isinstance(entities.get("urls"), list):
        entities["urls"] = [strip_url_query(str(u)) for u in entities["urls"]]
    payload["text_dropped"] = reason
    return json.dumps(payload, ensure_ascii=False)


def _with_dropped(metadata_json: str | None, reason: str) -> str:
    try:
        payload = json.loads(metadata_json) if metadata_json else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["text_dropped"] = reason
    return json.dumps(payload, ensure_ascii=False)


def scrub_capture_rows(conn: sqlite3.Connection) -> dict[str, int]:
    """Apply the gate to stored capture rows. Returns counts by reason (never content)."""
    privacy = PrivacyConfig()
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        last = 0
        while True:
            rows = conn.execute(
                "SELECT id, source, app_name, process_name, window_title, content_text, metadata_json "
                f"FROM raw_events WHERE id > ? AND source IN ({_marks(_GATED_SOURCES)}) ORDER BY id LIMIT ?",
                (last, *_GATED_SOURCES, _BATCH),
            ).fetchall()
            if not rows:
                break
            last = rows[-1]["id"]
            deletes: list[tuple] = []
            updates: list[tuple] = []
            for r in rows:
                reason = None
                action = None
                if is_blacklisted_app(
                    r["app_name"], r["process_name"],
                    bundle_ids=privacy.blacklist_bundle_ids,
                    patterns=privacy.blacklist_patterns,
                    app_names=privacy.blacklist_app_names,
                ):
                    action, reason = DROP, "blacklist"
                elif r["source"] in ("browser", "browser_url") and _denied_host(
                    r["metadata_json"], privacy.url_deny_hosts
                ):
                    action, reason = DROP, "url_deny_host"
                elif r["content_text"] is None and r["source"] in ("keyboard_chunk", "ax_text", "clipboard"):
                    continue  # already metadata-only: nothing left to gate (and "empty" must not delete it)
                else:
                    verdict = capture_gate(
                        source=r["source"],
                        app_name=r["app_name"],
                        process_name=r["process_name"],
                        window_title=r["window_title"],
                        content_text=r["content_text"],
                        url=_row_url(r["metadata_json"]),
                        title_deny_patterns=privacy.title_deny_patterns,
                    )
                    if verdict is not None:
                        action, reason = verdict.action, verdict.reason
                if action == DROP:
                    deletes.append((r["id"],))
                elif action == METADATA_ONLY:
                    clear_title = reason != "terminal_app"
                    if r["content_text"] is None and (not clear_title or r["window_title"] is None):
                        continue  # already reduced
                    meta = r["metadata_json"]
                    if r["source"] == "browser_url":
                        meta = _strip_urls(meta, reason)
                    else:
                        meta = _with_dropped(meta, reason)
                    updates.append((None, None, None if clear_title else r["window_title"], meta, r["id"]))
                else:
                    continue
                counts[reason] = counts.get(reason, 0) + 1
            if updates:
                conn.executemany(
                    "UPDATE raw_events SET content_text = ?, content_hash = ?, window_title = ?, "
                    "metadata_json = ? WHERE id = ?",
                    updates,
                )
            if deletes:
                conn.executemany("DELETE FROM raw_events WHERE id = ?", deletes)
                conn.executemany(
                    "DELETE FROM search_docs WHERE ref_type IN ('clipboard','manual') AND ref_id = CAST(? AS TEXT)",
                    deletes,
                )
            if updates:
                conn.executemany(
                    "DELETE FROM search_docs WHERE ref_type IN ('clipboard','manual') AND ref_id = CAST(? AS TEXT)",
                    [(u[4],) for u in updates],
                )
    finally:
        conn.row_factory = previous
    return counts


def migrate_v24(conn: sqlite3.Connection) -> None:
    assert _GATED_SOURCES and set(_GATED_SOURCES) <= CAPTURE_SOURCES
    scrub_capture_rows(conn)
