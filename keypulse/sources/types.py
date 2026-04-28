from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator


FIELD_HEURISTICS: dict[str, set[str]] = {
    "intent_text": {"text", "content", "body", "message", "prompt", "query", "input", "command"},
    "ai_dialog": {"role", "user", "assistant", "completion", "response", "model"},
    "session": {"session_id", "conversation_id", "thread_id", "chat_id"},
    "nav": {"title", "url", "visit", "history", "bookmark", "domain"},
    "comm": {"from", "to", "sender", "recipient", "subject", "cc", "bcc"},
    "time": {"timestamp", "ts", "created_at", "updated_at", "time", "date"},
    "artifact": {"path", "file", "document", "project", "repo", "filename"},
    "state": {"status", "state", "action", "event", "type"},
}


def classify_fields(field_names: Iterable[str]) -> dict[str, list[str]]:
    """返回 {category: [matched_fields]}"""
    hits: dict[str, set[str]] = {category: set() for category in FIELD_HEURISTICS}
    for raw in field_names:
        if not isinstance(raw, str) or not raw:
            continue
        normalized = raw.strip().lower().replace("-", "_")
        for category, keywords in FIELD_HEURISTICS.items():
            if normalized in keywords:
                hits[category].add(raw)
    return {category: sorted(values) for category, values in hits.items() if values}


def confidence_from_categories(num_categories: int) -> str:
    if num_categories >= 3:
        return "high"
    if num_categories >= 1:
        return "medium"
    return "low"


@dataclass
class SemanticEvent:
    time: datetime
    source: str
    actor: str
    intent: str
    artifact: str
    raw_ref: str
    privacy_tier: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("SemanticEvent.time must be timezone-aware")
        self.time = self.time.astimezone(timezone.utc)


@dataclass
class DataSourceInstance:
    plugin: str
    locator: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DataSource(ABC):
    name: str
    privacy_tier: str
    liveness: str
    description: str = ""

    @abstractmethod
    def discover(self) -> list[DataSourceInstance]:
        pass

    @abstractmethod
    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        pass
