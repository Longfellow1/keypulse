from __future__ import annotations

from pathlib import Path

from penpal.sources.cleaning.config import load_cleaning_config, matches_any_pattern


def is_excluded_path(path: Path) -> tuple[bool, str]:
    normalized = str(_expand_current_home(path).resolve(strict=False))
    lowered = normalized.lower()
    config = load_cleaning_config()

    for pattern in config.path_exclude_patterns:
        expanded_patterns = _expand_path_pattern(pattern)
        lowered_patterns = tuple(item.lower() for item in expanded_patterns)
        if matches_any_pattern(normalized, expanded_patterns) or matches_any_pattern(lowered, lowered_patterns):
            return True, f"matched:{pattern}"
    return False, ""


def _expand_path_pattern(pattern: str) -> tuple[str, ...]:
    cleaned = pattern.strip()
    if cleaned.startswith("~/"):
        return (cleaned, str(Path.home() / cleaned[2:]))
    if cleaned == "~":
        return (cleaned, str(Path.home()))
    if cleaned.startswith("~"):
        return (cleaned, str(Path(cleaned).expanduser()))
    return (cleaned,)


def _expand_current_home(path: Path) -> Path:
    value = str(path)
    if value == "~":
        return Path.home()
    if value.startswith("~/"):
        return Path.home() / value[2:]
    if value.startswith("~"):
        return path.expanduser()
    return path
