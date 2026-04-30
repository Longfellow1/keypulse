from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PATH_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "*backup*",
    "*.bak",
    "*backup-202*",
    "*node_modules*",
    "*__pycache__*",
    "*.git/objects*",
    "*Cache*",
    "*.cache*",
    "*.Trash*",
    "*Library/Caches*",
    "*Library/Logs/Diagnostic*",
    "*Crash Reports*",
    "*crashpad*",
    "*.tmp",
    "*.temp",
)

DEFAULT_SHORT_COMMAND_BLACKLIST: tuple[str, ...] = (
    "ls",
    "ll",
    "la",
    "cd",
    "cd ..",
    "cd -",
    "pwd",
    "clear",
    "exit",
    "q",
    "j",
    "k",
    "c",
    "..",
    "~",
    ":q",
    ":wq",
    "up",
    "down",
)


PRIVACY_TIERS: tuple[str, ...] = ("green", "yellow", "red")


@dataclass(frozen=True)
class CleaningConfig:
    path_exclude_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_PATH_EXCLUDE_PATTERNS)
    short_command_blacklist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SHORT_COMMAND_BLACKLIST)
    dedup_time_window_minutes: int = 10
    privacy_max_tier: str = "yellow"


def load_cleaning_config() -> CleaningConfig:
    defaults = CleaningConfig()
    path = Path.home() / ".keypulse" / "cleaning.toml"
    if not path.exists():
        return defaults
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    path_patterns = _read_str_list(parsed, "path_exclude_patterns") or list(defaults.path_exclude_patterns)
    short_commands = _read_str_list(parsed, "short_command_blacklist") or list(defaults.short_command_blacklist)
    dedup_window = _read_int(parsed, "dedup_time_window_minutes", defaults.dedup_time_window_minutes)
    privacy_max_tier = _read_privacy_tier(parsed, "privacy_max_tier", defaults.privacy_max_tier)
    return CleaningConfig(
        path_exclude_patterns=tuple(path_patterns),
        short_command_blacklist=tuple(short_commands),
        dedup_time_window_minutes=max(1, dedup_window),
        privacy_max_tier=privacy_max_tier,
    )


def matches_any_pattern(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _read_str_list(parsed: dict[str, object], key: str) -> list[str]:
    value = parsed.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
    return []


def _read_int(parsed: dict[str, object], key: str, default: int) -> int:
    value = parsed.get(key)
    if isinstance(value, int):
        return value
    return default


def _read_privacy_tier(parsed: dict[str, object], key: str, default: str) -> str:
    value = parsed.get(key)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in PRIVACY_TIERS:
            return normalized
    return default
