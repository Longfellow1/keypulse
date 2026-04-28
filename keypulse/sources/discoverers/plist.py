from __future__ import annotations

import plistlib
from pathlib import Path

from keypulse.sources.discoverers import CandidateSource
from keypulse.sources.cleaning.path_filter import is_excluded_path


_RECENT_KEYWORDS = ("recent", "history", "recentfiles")


def discover_plist_candidates(*, exclude_paths: set[str]) -> list[CandidateSource]:
    pref_dir = Path.home() / "Library" / "Preferences"
    if not pref_dir.exists():
        return []

    candidates: list[CandidateSource] = []
    for path in sorted(pref_dir.glob("*.plist")):
        resolved = path.expanduser().resolve(strict=False)
        excluded, _ = is_excluded_path(resolved)
        if excluded:
            continue
        if _is_excluded(resolved, exclude_paths):
            continue
        candidate = _candidate_for(resolved)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _candidate_for(path: Path) -> CandidateSource | None:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    hit_keys = sorted(_collect_recent_keys(data))
    if not hit_keys:
        return None

    confidence = "high" if any(key == "NSRecentDocuments" for key in hit_keys) else "medium"
    return CandidateSource(
        discoverer="plist",
        path=str(path),
        app_hint=_infer_app_hint(path),
        schema_signature=",".join(sorted(str(key) for key in data.keys())),
        hint_tables=hit_keys,
        confidence=confidence,
    )


def _collect_recent_keys(value: object) -> set[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            normalized = lowered.replace("_", "")
            if key == "NSRecentDocuments" or any(keyword in normalized for keyword in _RECENT_KEYWORDS):
                hits.add(key)
            hits.update(_collect_recent_keys(nested))
    elif isinstance(value, list):
        for item in value:
            hits.update(_collect_recent_keys(item))
    return hits


def _infer_app_hint(path: Path) -> str:
    stem = path.stem
    if not stem:
        return "unknown"
    parts = stem.split(".")
    if len(parts) >= 3 and parts[0] == "com" and parts[1] == "apple":
        return parts[-1] or "unknown"
    return parts[-1] or stem


def _is_excluded(path: Path, exclude_paths: set[str]) -> bool:
    for excluded in exclude_paths:
        excluded_path = Path(excluded)
        if path == excluded_path or excluded_path in path.parents:
            return True
    return False
