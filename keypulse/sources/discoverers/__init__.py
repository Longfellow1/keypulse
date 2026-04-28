from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CandidateSource:
    """通用扫描发现的候选金矿（不是已识别的 plugin）"""

    discoverer: str
    path: str
    app_hint: str
    schema_signature: str
    hint_tables: list[str] = field(default_factory=list)
    hint_fields: list[str] = field(default_factory=list)
    confidence: str = "low"


def discover_all_candidates(*, exclude_paths: set[str]) -> dict[str, list[CandidateSource]]:
    from keypulse.sources.discoverers.json_files import discover_json_files_candidates
    from keypulse.sources.discoverers.jsonl import discover_jsonl_candidates
    from keypulse.sources.discoverers.leveldb import discover_leveldb_candidates
    from keypulse.sources.discoverers.plist import discover_plist_candidates
    from keypulse.sources.discoverers.sqlite import discover_sqlite_candidates

    normalized = _normalize_paths(exclude_paths)
    return {
        "sqlite": discover_sqlite_candidates(exclude_paths=normalized),
        "leveldb": discover_leveldb_candidates(exclude_paths=normalized),
        "jsonl": discover_jsonl_candidates(exclude_paths=normalized),
        "json_files": discover_json_files_candidates(exclude_paths=normalized),
        "plist": discover_plist_candidates(exclude_paths=normalized),
    }


def _normalize_paths(paths: set[str]) -> set[str]:
    normalized: set[str] = set()
    for raw in paths:
        try:
            normalized.add(str(Path(raw).expanduser().resolve(strict=False)))
        except Exception:
            continue
    return normalized


__all__ = ["CandidateSource", "discover_all_candidates"]
