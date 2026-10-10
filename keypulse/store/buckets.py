"""Bucket assignment (V1 PRD §3.4.2, D23): per-source default + app rules, no content classification.

Resolution order for an event:
  1. enabled `policies` rows with mode='bucket' (scope_type 'app' or 'source'), by priority then id.
     config_json = {"bucket": "<Name>"}. App scope matches app_name / process_name by
     case-insensitive substring (same semantics as the privacy PolicyEngine); source scope is exact.
  2. config `[buckets.source_defaults]` overrides, then DEFAULT_SOURCE_BUCKETS.
  3. otherwise None: an unmapped source is never auto-placed in a shareable bucket (PRD: uncertain
     origin must not default to a low-sensitivity bucket), so no grant can read it.

The bucket is stamped on the row at ingest; changing rules is not retroactive until
`reapply_buckets()` (CLI: `keypulse rules rebucket`) is run.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

BUCKETS = ("Work", "Personal", "Private", "Financial")  # Temporary is P1 (D23)
BUCKET_MODE = "bucket"  # policies.mode value marking a bucket rule (ignored by the privacy PolicyEngine)

DEFAULT_SOURCE_BUCKETS: dict[str, str] = {
    "claude_code": "Work",
    "codex_cli": "Work",
    "git_log": "Work",
    "zsh_history": "Work",
    "markdown_vault": "Personal",
    "chrome_history": "Personal",
    "safari_history": "Personal",
    "wechat": "Personal",
    "knowledgec": "Personal",
}

_overrides: Optional[dict[str, str]] = None


def normalize_bucket(name: str) -> str:
    """Canonical bucket name (case-insensitive) or ValueError."""
    for b in BUCKETS:
        if (name or "").strip().lower() == b.lower():
            return b
    raise ValueError(f"unknown bucket {name!r}; choose from {', '.join(BUCKETS)}")


def reset_cache() -> None:
    global _overrides
    _overrides = None


def _config_overrides() -> dict[str, str]:
    global _overrides
    if _overrides is None:
        found: dict[str, str] = {}
        try:
            from keypulse.config import Config

            for src, bucket in Config.load().buckets.source_defaults.items():
                try:
                    found[src] = normalize_bucket(bucket)
                except ValueError:
                    continue  # ignore invalid config entries rather than failing ingest
        except Exception:
            found = {}
        _overrides = found
    return _overrides


def default_bucket_for_source(source: Optional[str], overrides: Optional[dict[str, str]] = None) -> Optional[str]:
    if not source:
        return None
    merged = _config_overrides() if overrides is None else overrides
    return merged.get(source) or DEFAULT_SOURCE_BUCKETS.get(source)


def load_bucket_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT scope_type, scope_value, config_json FROM policies "
        "WHERE enabled = 1 AND mode = ? AND scope_type IN ('app', 'source') "
        "ORDER BY priority ASC, id ASC",
        (BUCKET_MODE,),
    ).fetchall()
    rules = []
    for r in rows:
        try:
            bucket = normalize_bucket(json.loads(r["config_json"] or "{}").get("bucket", ""))
        except (ValueError, TypeError, AttributeError):
            continue
        rules.append({"scope_type": r["scope_type"], "scope_value": r["scope_value"], "bucket": bucket})
    return rules


def resolve_bucket(
    source: Optional[str],
    app_name: Optional[str],
    process_name: Optional[str] = None,
    *,
    rules: list[dict],
    overrides: Optional[dict[str, str]] = None,
) -> Optional[str]:
    for rule in rules:
        value = (rule["scope_value"] or "").lower()
        if not value:
            continue
        if rule["scope_type"] == "app":
            if any(value in (name or "").lower() for name in (app_name, process_name)):
                return rule["bucket"]
        elif rule["scope_type"] == "source" and source == rule["scope_value"]:
            return rule["bucket"]
    return default_bucket_for_source(source, overrides)


def resolve_for_event(conn: sqlite3.Connection, source, app_name, process_name=None) -> Optional[str]:
    return resolve_bucket(source, app_name, process_name, rules=load_bucket_rules(conn))


def reapply_buckets(conn: sqlite3.Connection, *, only_missing: bool = False) -> int:
    """Re-evaluate buckets for stored rows with current rules; returns rows changed."""
    rules = load_bucket_rules(conn)
    overrides = _config_overrides()
    where = " WHERE bucket IS NULL" if only_missing else ""
    changed = 0
    for row in conn.execute(f"SELECT id, source, app_name, process_name, bucket FROM raw_events{where}").fetchall():
        new = resolve_bucket(row["source"], row["app_name"], row["process_name"], rules=rules, overrides=overrides)
        if new != row["bucket"]:
            conn.execute("UPDATE raw_events SET bucket = ? WHERE id = ?", (new, row["id"]))
            changed += 1
    conn.commit()
    return changed
