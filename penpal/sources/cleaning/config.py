from __future__ import annotations

import fnmatch
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from penpal.utils.logging import get_logger
import penpal.config as config_io


LOGGER = get_logger("sources.cleaning.config")


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
    "~/Library/Caches/*",
    "~/Library/Containers/*",
    "~/Library/Application Support/*",
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
    # MCP server names Penpal is registered under in the user's AI hosts (via_penpal marking).
    penpal_server_names: tuple[str, ...] = ("penpal", "keypulse")


class CleaningConfigError(RuntimeError):
    """The cleaning rules could not be loaded safely, so source import must stop."""


def load_cleaning_config() -> CleaningConfig:
    defaults = CleaningConfig()
    path = config_io.get_data_dir() / "cleaning.toml"
    try:
        parsed = config_io.read_cleaning_config(path)
    except OSError as exc:
        _raise_cleaning_config_error(path, "无法读取", exc)
    except UnicodeDecodeError as exc:
        _raise_cleaning_config_error(path, "编码无效", exc)
    except tomllib.TOMLDecodeError as exc:
        _raise_cleaning_config_error(path, "TOML 格式无效", exc)

    path_patterns = _read_str_list(parsed, "path_exclude_patterns") or list(defaults.path_exclude_patterns)
    short_commands = _read_str_list(parsed, "short_command_blacklist") or list(defaults.short_command_blacklist)
    dedup_window = _read_int(parsed, "dedup_time_window_minutes", defaults.dedup_time_window_minutes)
    privacy_max_tier = _read_privacy_tier(parsed, "privacy_max_tier", defaults.privacy_max_tier)
    server_names = _read_str_list(parsed, "penpal_server_names")
    if not server_names:
        server_names = _read_str_list(parsed, "keypulse_server_names")
        if server_names:
            print(
                "Deprecated cleaning config key keypulse_server_names; use penpal_server_names instead.",
                file=sys.stderr,
            )
    return CleaningConfig(
        path_exclude_patterns=tuple(path_patterns),
        short_command_blacklist=tuple(short_commands),
        dedup_time_window_minutes=max(1, dedup_window),
        privacy_max_tier=privacy_max_tier,
        penpal_server_names=tuple(server_names or defaults.penpal_server_names),
    )


def _raise_cleaning_config_error(path: Path, reason: str, exc: Exception) -> None:
    error_type = type(exc).__name__
    LOGGER.error("cleaning config %s (%s); import stopped", reason, error_type)
    raise CleaningConfigError(f"清理配置文件 {path.name}{reason}（{error_type}），已停止导入。") from exc


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
