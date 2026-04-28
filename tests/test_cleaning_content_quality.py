from __future__ import annotations

from datetime import datetime, timezone

from keypulse.sources.cleaning.content_quality import is_low_signal_event
from keypulse.sources.types import SemanticEvent


def _event(source: str, intent: str, artifact: str = '', raw_ref: str = '') -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        source=source,
        actor='u',
        intent=intent,
        artifact=artifact,
        raw_ref=raw_ref,
        privacy_tier='green',
        metadata={},
    )


def test_content_quality_filters_short_zsh_command() -> None:
    assert is_low_signal_event(_event('zsh_history', 'ls'))[0]


def test_content_quality_filters_browser_blank_event() -> None:
    assert is_low_signal_event(_event('chrome_history', '', 'about:blank'))[0]


def test_content_quality_filters_markdown_self_reference() -> None:
    assert is_low_signal_event(_event('markdown_vault', 'x', '/vault/Events/2026-04-28/片段-a.md'))[0]


def test_content_quality_keeps_meaningful_event() -> None:
    assert is_low_signal_event(_event('zsh_history', 'pytest tests/test_sources_registry.py'))[0] is False
