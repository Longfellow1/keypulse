from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.max.replace(tzinfo=timezone.utc)


def _title(event: dict[str, Any]) -> str:
    explicit_title = str(
        event.get("title")
        or event.get("window_title")
        or event.get("app_name")
        or ""
    ).strip()
    if explicit_title:
        return explicit_title

    body = _body(event)
    if str(event.get("source") or "") in {"manual", "clipboard"} and body.strip():
        return " ".join(body.strip().split())[:72]

    return str(event.get("event_type") or "event")


def _body(event: dict[str, Any]) -> str:
    return str(
        event.get("body")
        or event.get("content_text")
        or event.get("window_title")
        or event.get("app_name")
        or ""
    )


def _importance(event: dict[str, Any]) -> float:
    source = str(event.get("source") or "")
    if source == "manual":
        return 1.0
    if source == "clipboard":
        return 0.85
    if source == "window":
        return 0.65
    return 0.5


def _is_draft_worthy(event: dict[str, Any]) -> bool:
    source = str(event.get("source") or "")
    event_type = str(event.get("event_type") or "")
    title = _title(event).strip()
    body = _body(event).strip()

    if event_type in {"idle_start", "idle_end"}:
        return False
    if source in {"manual", "clipboard"}:
        return bool(title or body)
    if source == "window" and title and body and title == body and len(title) <= 12:
        return False
    return bool(title or body)


@dataclass(frozen=True)
class RecordEvent:
    ts: str
    source: str
    event_type: str
    title: str
    body: str
    importance: float


def normalize_record_events(items: list[dict[str, Any]]) -> list[RecordEvent]:
    normalized = [
        RecordEvent(
            ts=str(item.get("ts_start") or item.get("created_at") or ""),
            source=str(item.get("source") or ""),
            event_type=str(item.get("event_type") or ""),
            title=_title(item),
            body=_body(item),
            importance=_importance(item),
        )
        for item in items
        if item is not None and _is_draft_worthy(item)
    ]
    return sorted(normalized, key=lambda item: (_parse_ts(item.ts), -item.importance, item.title.lower()))
