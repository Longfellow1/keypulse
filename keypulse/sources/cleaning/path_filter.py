from __future__ import annotations

from pathlib import Path

from keypulse.sources.cleaning.config import load_cleaning_config, matches_any_pattern


def is_excluded_path(path: Path) -> tuple[bool, str]:
    normalized = str(path.expanduser().resolve(strict=False))
    lowered = normalized.lower()
    config = load_cleaning_config()

    for pattern in config.path_exclude_patterns:
        if matches_any_pattern(normalized, (pattern,)) or matches_any_pattern(lowered, (pattern.lower(),)):
            return True, f"matched:{pattern}"
    return False, ""
