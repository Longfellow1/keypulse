from __future__ import annotations

from datetime import timedelta

from keypulse.sources.types import SemanticEvent


def dedup_events(events: list[SemanticEvent], *, time_window_minutes: int = 10) -> list[SemanticEvent]:
    if not events:
        return []

    sorted_events = sorted(events, key=lambda event: event.time)
    window = timedelta(minutes=max(1, time_window_minutes))
    survivors: list[SemanticEvent] = []

    for event in sorted_events:
        merged = False
        key = _dedup_key(event)
        for existing in reversed(survivors):
            if _dedup_key(existing) != key:
                continue
            if _is_browser_event(event) and event.time.date() == existing.time.date():
                in_window = True
            else:
                in_window = event.time - existing.time <= window
            if not in_window:
                break
            if _should_not_dedup(existing, event):
                continue
            existing.metadata = dict(existing.metadata or {})
            existing.metadata["dedup_count"] = int(existing.metadata.get("dedup_count", 1)) + 1
            merged = True
            break
        if not merged:
            event.metadata = dict(event.metadata or {})
            event.metadata.setdefault("dedup_count", 1)
            survivors.append(event)
    return survivors


def _dedup_key(event: SemanticEvent) -> tuple[str, str, str]:
    return (
        event.source,
        _normalize_intent(event.intent),
        _normalize_artifact(event.artifact),
    )


def _normalize_intent(value: str) -> str:
    return str(value or "").strip().lower()[:50]


def _normalize_artifact(value: str) -> str:
    return str(value or "").split("?", 1)[0].split("#", 1)[0].strip().lower()


def _should_not_dedup(left: SemanticEvent, right: SemanticEvent) -> bool:
    left_meta = left.metadata or {}
    right_meta = right.metadata or {}

    if left_meta.get("session_id") != right_meta.get("session_id"):
        return True

    if left.source == "claude_code":
        if left_meta.get("message_uuid") != right_meta.get("message_uuid"):
            return True

    if left.source == "git_log":
        if left.artifact != right.artifact:
            return True

    return False


def _is_browser_event(event: SemanticEvent) -> bool:
    return event.source in {"chrome_history", "safari_history"}
