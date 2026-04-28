from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from keypulse.sources.discoverers import CandidateSource


_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".vscdb")
_HINT_KEYWORDS = (
    "messages",
    "chats",
    "history",
    "sessions",
    "conversations",
    "events",
    "items",
    "prompts",
    "queries",
    "commands",
)


def discover_sqlite_candidates(*, exclude_paths: set[str]) -> list[CandidateSource]:
    home = Path.home()
    deadline = time.monotonic() + 30.0
    candidates: list[CandidateSource] = []

    app_support_root = home / "Library" / "Application Support"
    for path in _iter_sqlite_paths(app_support_root, max_depth=5, deadline=deadline):
        candidate = _candidate_for(path, exclude_paths)
        if candidate is not None:
            candidates.append(candidate)

    containers_root = home / "Library" / "Containers"
    if containers_root.exists():
        for container_dir in containers_root.iterdir():
            if time.monotonic() >= deadline:
                break
            data_root = container_dir / "Data"
            for path in _iter_sqlite_paths(data_root, max_depth=8, deadline=deadline):
                candidate = _candidate_for(path, exclude_paths)
                if candidate is not None:
                    candidates.append(candidate)

    return sorted(candidates, key=lambda item: item.path)


def _iter_sqlite_paths(root: Path, *, max_depth: int, deadline: float) -> Iterator[Path]:
    if not root.exists():
        return

    for dirpath, dirnames, filenames in os.walk(root):
        if time.monotonic() >= deadline:
            return
        current_dir = Path(dirpath)
        try:
            depth = len(current_dir.relative_to(root).parts)
        except Exception:
            continue
        if depth >= max_depth:
            dirnames[:] = []

        for filename in filenames:
            if time.monotonic() >= deadline:
                return
            if not filename.lower().endswith(_SQLITE_SUFFIXES):
                continue
            file_path = current_dir / filename
            try:
                file_depth = len(file_path.relative_to(root).parts) - 1
            except Exception:
                continue
            if file_depth > max_depth:
                continue
            yield file_path


def _candidate_for(path: Path, exclude_paths: set[str]) -> CandidateSource | None:
    resolved = path.expanduser().resolve(strict=False)
    if _is_excluded(resolved, exclude_paths):
        return None
    if not _is_sqlite_file(resolved):
        return None

    tables = _read_table_names(resolved)
    if not tables:
        return None

    hint_tables = sorted({name for name in tables if _is_hint_table(name)})
    if not hint_tables:
        return None

    confidence = "high" if len(hint_tables) >= 3 else "medium"
    return CandidateSource(
        discoverer="sqlite",
        path=str(resolved),
        app_hint=_infer_app_hint(resolved),
        schema_signature=",".join(sorted(tables)),
        hint_tables=hint_tables,
        confidence=confidence,
    )


def _is_excluded(path: Path, exclude_paths: set[str]) -> bool:
    for excluded in exclude_paths:
        excluded_path = Path(excluded)
        if path == excluded_path or excluded_path in path.parents:
            return True
    return False


def _is_sqlite_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except Exception:
        return False
    return header == _SQLITE_MAGIC


def _read_table_names(path: Path) -> list[str]:
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path), uri=False)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()

    names: list[str] = []
    for row in rows:
        if not row:
            continue
        name = row[0]
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(set(names))


def _is_hint_table(table_name: str) -> bool:
    lowered = table_name.lower()
    return any(keyword in lowered for keyword in _HINT_KEYWORDS)


def _infer_app_hint(path: Path) -> str:
    parts = path.parts
    if "Application Support" in parts:
        idx = parts.index("Application Support")
        if idx + 1 < len(parts):
            return _normalize_app_name(parts[idx + 1])
    if "Containers" in parts:
        idx = parts.index("Containers")
        if idx + 1 < len(parts):
            return _normalize_app_name(parts[idx + 1])
    return _normalize_app_name(path.parent.name)


def _normalize_app_name(raw: str) -> str:
    value = raw.strip()
    if not value:
        return "unknown"
    if "." in value:
        value = value.split(".")[-1]
    return value or "unknown"
