"""Migration v30: evidence rows for old markdown_vault rows, resolved through vault discovery.

Rows ingested before M2 have no evidence row (migration v28 could not locate them: their stored
metadata holds only the vault name and the vault-relative `artifact`, never an absolute path).
This pass rebuilds the absolute path from the vault roots that the markdown_vault plugin discovers
today (the same discovery read() uses; vault_name is the discovered label).

  * A row gets evidence only when its vault name maps to exactly one discovered root, the joined path
    stays inside that root after resolving (no `..` or symlink escape), and the file exists now.
  * Otherwise nothing is written for the row, the same as v28 does for unresolvable rows.
  * The locator is path.resolve() of the file, the same string new ingest writes (line 134 of the plugin).
  * Idempotent: only rows without any evidence are considered; run_migrations wraps this in one
    transaction together with the version stamp. Discovery runs only when a candidate row exists.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from penpal.foundation.approval_store import ApprovalStore
from penpal.foundation.source_roots import approved_markdown_vault_roots

LOGGER = logging.getLogger(__name__)
_BATCH = 2000
_SOURCE = "markdown_vault"


def _meta(raw: object) -> dict:
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _file_locator(root: Path, rel: object) -> Optional[str]:
    """Absolute, resolved path of root/rel when it is an existing file inside root; else None."""
    if not isinstance(rel, str) or not rel or "\x00" in rel or rel.startswith("/"):
        return None
    base = root.resolve(strict=False)
    target = (base / rel).resolve(strict=False)
    if target == base or not target.is_relative_to(base) or not target.is_file():
        return None
    return str(target)


def _rel_path(meta: dict, raw_ref: object, vault: str) -> object:
    """vault-relative path: metadata `artifact`, else the tail of raw_ref `markdown_vault:<vault>:<rel>`."""
    artifact = meta.get("artifact")
    if isinstance(artifact, str) and artifact:
        return artifact
    prefix = f"{_SOURCE}:{vault}:"
    if isinstance(raw_ref, str) and raw_ref.startswith(prefix):
        return raw_ref[len(prefix):]
    return None


def migrate_v30(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    now = datetime.now(timezone.utc).isoformat()
    roots: Optional[dict[str, Path]] = None  # discovered lazily, once per run
    created = skipped = 0
    last = 0
    while True:
        rows = conn.execute(
            "SELECT e.id, e.content_text, e.metadata_json FROM raw_events e "
            "WHERE e.event_type = 'semantic_event' AND e.source = ? AND e.id > ? "
            "AND NOT EXISTS (SELECT 1 FROM evidence ev WHERE ev.event_id = e.id) "
            "ORDER BY e.id LIMIT ?",
            (_SOURCE, last, _BATCH),
        ).fetchall()
        if not rows:
            break
        inserts = []
        for rid, text, meta_json in rows:
            meta = _meta(meta_json)
            vault = meta.get("vault_name")
            if roots is None and isinstance(vault, str):
                found: dict[str, list[Path]] = {}
                try:
                    approved = ApprovalStore().list_approved()
                except Exception:
                    approved = []
                for root in approved_markdown_vault_roots(approved):
                    if root.exists() and root.is_dir():
                        found.setdefault(root.name or "vault", []).append(root)
                roots = {name: paths[0] for name, paths in found.items() if len(paths) == 1}
            root = (roots or {}).get(vault) if isinstance(vault, str) else None
            rel = _rel_path(meta, meta.get("raw_ref"), vault if isinstance(vault, str) else "")
            locator = _file_locator(root, rel) if root is not None else None
            if locator is None:
                skipped += 1
                continue
            digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
            inserts.append((rid, locator, None, digest, 1, now))
            created += 1
        if inserts:
            conn.executemany(
                "INSERT INTO evidence(event_id, locator, offset_or_line, content_hash, available, created_at) "
                "VALUES (?,?,?,?,?,?)",
                inserts,
            )
        last = rows[-1][0]
    LOGGER.info("migration v30 markdown_vault evidence backfill: %d created, %d left without evidence",
                created, skipped)
    return {_SOURCE: (created, skipped)} if created or skipped else {}
