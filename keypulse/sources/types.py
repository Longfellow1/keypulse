from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


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
    normalized_keywords: dict[str, set[str]] = {
        category: {_normalize_key(keyword) for keyword in keywords}
        for category, keywords in FIELD_HEURISTICS.items()
    }
    hits: dict[str, set[str]] = {category: set() for category in FIELD_HEURISTICS}
    for raw in field_names:
        if not isinstance(raw, str) or not raw:
            continue
        normalized = _normalize_key(raw)
        for category, keywords in normalized_keywords.items():
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


class ContentShape(str, Enum):
    TABULAR_ROWS = "tabular_rows"
    KV_JSON_BLOB = "kv_json_blob"
    DOCUMENT_FILE = "document_file"

    def to_semantic_event(
        self,
        raw: Mapping[str, Any],
        *,
        source: str,
        privacy_tier: str,
    ) -> SemanticEvent | None:
        if self is ContentShape.TABULAR_ROWS:
            return _tabular_to_event(raw, source=source, privacy_tier=privacy_tier)
        if self is ContentShape.KV_JSON_BLOB:
            return _kv_blob_to_event(raw, source=source, privacy_tier=privacy_tier)
        if self is ContentShape.DOCUMENT_FILE:
            return _document_to_event(raw, source=source, privacy_tier=privacy_tier)
        return None


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
    app_hints: tuple[str, ...] = ()
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


def _tabular_to_event(
    raw: Mapping[str, Any],
    *,
    source: str,
    privacy_tier: str,
) -> SemanticEvent | None:
    parsed_time = _coerce_time(raw.get("time"))
    if parsed_time is None:
        return None
    actor = _to_text(raw.get("actor")) or "user"
    intent = _to_text(raw.get("intent")) or _to_text(raw.get("text")) or "row event"
    artifact = _to_text(raw.get("artifact")) or "row"
    raw_ref = _to_text(raw.get("raw_ref")) or f"{source}:row"
    metadata = _to_metadata(raw.get("metadata"), shape=ContentShape.TABULAR_ROWS.value)
    return SemanticEvent(
        time=parsed_time,
        source=source,
        actor=actor,
        intent=intent,
        artifact=artifact,
        raw_ref=raw_ref,
        privacy_tier=privacy_tier,
        metadata=metadata,
    )


def _kv_blob_to_event(
    raw: Mapping[str, Any],
    *,
    source: str,
    privacy_tier: str,
) -> SemanticEvent | None:
    blob = _coerce_json_obj(raw.get("value"))
    if blob is None:
        return None
    parsed_time = _extract_time(blob)
    if parsed_time is None:
        return None

    key = _to_text(raw.get("key")) or "unknown-key"
    actor = _extract_actor(blob) or "user"
    intent = _extract_intent(blob) or key
    artifact = _to_text(raw.get("artifact")) or key
    raw_ref = _to_text(raw.get("raw_ref")) or f"{source}:{key}"
    metadata = _to_metadata(
        raw.get("metadata"),
        shape=ContentShape.KV_JSON_BLOB.value,
        kv_key=key,
    )
    return SemanticEvent(
        time=parsed_time,
        source=source,
        actor=actor,
        intent=intent[:200],
        artifact=artifact,
        raw_ref=raw_ref,
        privacy_tier=privacy_tier,
        metadata=metadata,
    )


def _document_to_event(
    raw: Mapping[str, Any],
    *,
    source: str,
    privacy_tier: str,
) -> SemanticEvent | None:
    parsed_time = _coerce_time(raw.get("time") or raw.get("mtime"))
    if parsed_time is None:
        return None
    path_text = _to_text(raw.get("path")) or "document"
    actor = _to_text(raw.get("actor")) or "user"
    intent = _to_text(raw.get("intent")) or Path(path_text).stem or "document"
    artifact = _to_text(raw.get("artifact")) or path_text
    raw_ref = _to_text(raw.get("raw_ref")) or f"{source}:{artifact}"
    metadata = _to_metadata(raw.get("metadata"), shape=ContentShape.DOCUMENT_FILE.value)
    return SemanticEvent(
        time=parsed_time,
        source=source,
        actor=actor,
        intent=intent[:200],
        artifact=artifact,
        raw_ref=raw_ref,
        privacy_tier=privacy_tier,
        metadata=metadata,
    )


def _coerce_json_obj(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_time(payload: Mapping[str, Any]) -> datetime | None:
    candidates = {"timestamp", "ts", "createdat", "updatedat", "time", "date"}
    normalized_payload = _normalized_payload(payload)
    for key in candidates:
        if key not in normalized_payload:
            continue
        parsed = _coerce_time(normalized_payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_intent(payload: Mapping[str, Any]) -> str:
    candidates = ("text", "content", "body", "message", "prompt", "query", "input", "command")
    normalized_payload = _normalized_payload(payload)
    for key in candidates:
        value = _to_text(normalized_payload.get(_normalize_key(key)))
        if value:
            return value
    return ""


def _extract_actor(payload: Mapping[str, Any]) -> str:
    normalized_payload = _normalized_payload(payload)
    for key in ("role", "author", "sender", "actor", "type"):
        value = _to_text(normalized_payload.get(_normalize_key(key)))
        if value:
            return value
    return ""


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    text = str(value).strip()
    return text


def _to_metadata(raw: Any, **extra: Any) -> dict[str, Any]:
    metadata = dict(raw) if isinstance(raw, dict) else {}
    metadata.update(extra)
    return metadata


def _coerce_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            try:
                value = float(text)
            except ValueError:
                return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 1_000_000_000_000 < numeric < 4_000_000_000_000:
            return datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
        if 11_000_000_000_000_000 < numeric < 14_000_000_000_000_000:
            return datetime.fromtimestamp((numeric / 1_000_000) - 11_644_473_600, tz=timezone.utc)
        if 0 < numeric < 4_000_000_000:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
    return None


def _normalize_key(raw: str) -> str:
    return "".join(ch for ch in raw.strip().lower() if ch.isalnum())


def _normalized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        normalized_key = _normalize_key(key)
        if not normalized_key:
            continue
        if normalized_key in normalized:
            continue
        normalized[normalized_key] = value
    return normalized
