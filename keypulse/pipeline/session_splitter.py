from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from keypulse.sources.types import SemanticEvent


@dataclass
class ActivitySession:
    id: str
    time_start: datetime
    time_end: datetime
    events: list[SemanticEvent]


def split_into_sessions(
    events: list[SemanticEvent],
    *,
    idle_threshold_minutes: int = 30,
) -> list[ActivitySession]:
    """Split ordered activity stream by idle gaps larger than threshold."""
    if not events:
        return []

    ordered = sorted(events, key=lambda event: event.time)
    threshold = timedelta(minutes=max(0, idle_threshold_minutes))
    sessions: list[ActivitySession] = []
    bucket: list[SemanticEvent] = [ordered[0]]

    for event in ordered[1:]:
        if event.time - bucket[-1].time > threshold:
            session_index = len(sessions) + 1
            sessions.append(
                ActivitySession(
                    id=f"session-{session_index}",
                    time_start=bucket[0].time,
                    time_end=bucket[-1].time,
                    events=list(bucket),
                )
            )
            bucket = [event]
            continue
        bucket.append(event)

    session_index = len(sessions) + 1
    sessions.append(
        ActivitySession(
            id=f"session-{session_index}",
            time_start=bucket[0].time,
            time_end=bucket[-1].time,
            events=list(bucket),
        )
    )
    return sessions
