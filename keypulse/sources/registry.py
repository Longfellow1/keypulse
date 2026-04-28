from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from keypulse.sources.cleaning.config import load_cleaning_config
from keypulse.sources.cleaning.content_quality import is_low_signal_event
from keypulse.sources.cleaning.dedup import dedup_events
from keypulse.sources.plugins.chrome_history import ChromeHistorySource
from keypulse.sources.plugins.claude_code import ClaudeCodeSource
from keypulse.sources.plugins.codex_cli import CodexCliSource
from keypulse.sources.plugins.git_log import GitLogSource
from keypulse.sources.plugins.markdown_vault import MarkdownVaultSource
from keypulse.sources.plugins.safari_history import SafariHistorySource
from keypulse.sources.plugins.zsh_history import ZshHistorySource
from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent
from keypulse.utils.logging import get_logger


LOGGER = get_logger("sources.registry")

_PLUGINS: dict[str, DataSource] = {}


def register(source: DataSource) -> None:
    _PLUGINS[source.name] = source


def list_sources() -> list[DataSource]:
    return sorted(_PLUGINS.values(), key=lambda plugin: plugin.name)


def get_source(name: str) -> DataSource | None:
    return _PLUGINS.get(name)


def discover_all(source: str | None = None) -> dict[str, list[DataSourceInstance]]:
    selected = _select_sources(source)
    discovered: dict[str, list[DataSourceInstance]] = {}
    for plugin in selected:
        try:
            discovered[plugin.name] = plugin.discover()
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("discover failed for %s: %s", plugin.name, exc)
            discovered[plugin.name] = []
    return discovered


def read_all(
    since: datetime,
    until: datetime,
    *,
    source: str | None = None,
) -> Iterator[SemanticEvent]:
    since_utc = _as_utc(since)
    until_utc = _as_utc(until)

    events: list[SemanticEvent] = []
    discovered = discover_all(source)
    selected = _select_sources(source)
    cleaning_config = load_cleaning_config()

    for plugin in selected:
        instances = discovered.get(plugin.name, [])
        for instance in instances:
            try:
                stream = plugin.read(instance, since_utc, until_utc)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("read setup failed for %s (%s): %s", plugin.name, instance.locator, exc)
                continue
            try:
                for event in stream:
                    try:
                        is_noise, _ = is_low_signal_event(event)
                    except Exception as exc:  # pragma: no cover - defensive
                        LOGGER.warning("L3 filter failed for %s: %s", plugin.name, exc)
                        is_noise = False
                    if is_noise:
                        continue
                    events.append(event)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("read stream failed for %s (%s): %s", plugin.name, instance.locator, exc)

    try:
        deduped = dedup_events(events, time_window_minutes=cleaning_config.dedup_time_window_minutes)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("L4 dedup failed: %s", exc)
        deduped = events
    deduped.sort(key=lambda event: event.time)
    yield from deduped


def _select_sources(source: str | None) -> list[DataSource]:
    if source:
        plugin = _PLUGINS.get(source)
        return [plugin] if plugin is not None else []
    return list_sources()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


register(GitLogSource())
register(ClaudeCodeSource())
register(CodexCliSource())
register(ChromeHistorySource())
register(SafariHistorySource())
register(ZshHistorySource())
register(MarkdownVaultSource())
