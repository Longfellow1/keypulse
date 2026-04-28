from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.sources.cleaning.dedup import dedup_events
from keypulse.sources.types import SemanticEvent


def _event(minutes: int, *, source: str = 'chrome_history', intent: str = 'QQMail', artifact: str = 'https://wx.mail.qq.com/home/index', metadata: dict | None = None) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        source=source,
        actor='u',
        intent=intent,
        artifact=artifact,
        raw_ref='r',
        privacy_tier='green',
        metadata=metadata or {},
    )


def test_dedup_events_merges_same_url_in_window() -> None:
    events = [_event(0), _event(3), _event(8)]
    deduped = dedup_events(events, time_window_minutes=10)
    assert len(deduped) == 1
    assert deduped[0].metadata['dedup_count'] == 3


def test_dedup_events_respects_session_and_claude_uuid_exceptions() -> None:
    events = [
        _event(0, source='claude_code', metadata={'message_uuid': 'a', 'session_id': 's1'}),
        _event(1, source='claude_code', metadata={'message_uuid': 'b', 'session_id': 's1'}),
        _event(2, source='zsh_history', metadata={'session_id': 's1'}),
        _event(3, source='zsh_history', metadata={'session_id': 's2'}),
    ]
    deduped = dedup_events(events, time_window_minutes=10)
    assert len(deduped) == 4


def test_dedup_events_merges_browser_same_day_even_if_over_window() -> None:
    deduped = dedup_events([_event(0), _event(120)], time_window_minutes=10)
    assert len(deduped) == 1
    assert deduped[0].metadata["dedup_count"] == 2
