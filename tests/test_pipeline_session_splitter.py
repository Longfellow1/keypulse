from __future__ import annotations

from datetime import datetime, timezone

from keypulse.pipeline.session_splitter import split_into_sessions
from keypulse.sources.types import SemanticEvent


def _event(ts: str, *, source: str = "git_log", intent: str = "", artifact: str = "") -> SemanticEvent:
    return SemanticEvent(
        time=datetime.fromisoformat(ts),
        source=source,
        actor="Harland",
        intent=intent,
        artifact=artifact,
        raw_ref="",
        privacy_tier="green",
        metadata={},
    )


def test_split_into_sessions_empty() -> None:
    assert split_into_sessions([]) == []


def test_split_into_sessions_sorts_and_splits_on_idle_gap() -> None:
    events = [
        _event("2026-04-28T10:40:00+00:00", source="claude_code"),
        _event("2026-04-28T10:00:00+00:00", source="git_log"),
        _event("2026-04-28T10:20:00+00:00", source="zsh_history"),
        _event("2026-04-28T11:20:00+00:00", source="git_log"),
    ]

    sessions = split_into_sessions(events, idle_threshold_minutes=30)

    assert [session.id for session in sessions] == ["session-1", "session-2"]
    assert [event.source for event in sessions[0].events] == ["git_log", "zsh_history", "claude_code"]
    assert sessions[0].time_start == datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    assert sessions[0].time_end == datetime(2026, 4, 28, 10, 40, tzinfo=timezone.utc)
    assert sessions[1].time_start == datetime(2026, 4, 28, 11, 20, tzinfo=timezone.utc)
    assert sessions[1].time_end == datetime(2026, 4, 28, 11, 20, tzinfo=timezone.utc)


def test_split_into_sessions_keeps_boundary_gap_inside_same_session() -> None:
    events = [
        _event("2026-04-28T10:00:00+00:00"),
        _event("2026-04-28T10:30:00+00:00"),
    ]

    sessions = split_into_sessions(events, idle_threshold_minutes=30)

    assert len(sessions) == 1
    assert len(sessions[0].events) == 2
