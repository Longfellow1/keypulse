"""L1 Entity Extractor — standalone LLM module for raw-event entity mapping.

Step 2-1 scope:
- Read real events from raw_events
- Reuse existing source-aware capping
- Call cloud-capable ModelGateway with L1 prompt
- Do NOT wire into orchestrator yet
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from keypulse.config import Config
from keypulse.pipeline.event_intake import cap_events_by_source
from keypulse.pipeline.event_scoring import _flagship_event_score
from keypulse.pipeline.daily_strategy import build_prompt, extract_work_unit
from keypulse.pipeline.model import ModelGateway, load_model_gateway
from keypulse.prompts.loader import load_prompt
from keypulse.store.db import init_db
from keypulse.store.repository import query_raw_events
from keypulse.utils.dates import local_day_bounds, local_timezone


@dataclass(frozen=True)
class Entity:
    name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_event_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EventEntityMapping:
    event_id: str
    primary_entity: str
    secondary_entities: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_review: bool = False
    review_reason: str = ""


@dataclass(frozen=True)
class EntityExtractionResult:
    date: str
    entities: list[Entity] = field(default_factory=list)
    event_entity_map: list[EventEntityMapping] = field(default_factory=list)
    cross_entity_warning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "entities": [
                {
                    "name": e.name,
                    "type": e.type,
                    "aliases": e.aliases,
                    "confidence": e.confidence,
                    "evidence_event_ids": e.evidence_event_ids,
                }
                for e in self.entities
            ],
            "event_entity_map": [
                {
                    "event_id": m.event_id,
                    "primary_entity": m.primary_entity,
                    "secondary_entities": m.secondary_entities,
                    "confidence": m.confidence,
                    "needs_review": m.needs_review,
                    "review_reason": m.review_reason,
                }
                for m in self.event_entity_map
            ],
            "cross_entity_warning": self.cross_entity_warning,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EntityExtractionResult":
        entities_raw = payload.get("entities") if isinstance(payload, Mapping) else []
        entities: list[Entity] = []
        if isinstance(entities_raw, list):
            for item in entities_raw:
                if not isinstance(item, Mapping):
                    continue
                entities.append(
                    Entity(
                        name=str(item.get("name") or "").strip(),
                        type=str(item.get("type") or "unknown").strip() or "unknown",
                        aliases=[str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()],
                        confidence=_clamp_confidence(item.get("confidence")),
                        evidence_event_ids=[
                            str(event_id).strip()
                            for event_id in (item.get("evidence_event_ids") or [])
                            if str(event_id).strip()
                        ],
                    )
                )

        mapping_raw = payload.get("event_entity_map") if isinstance(payload, Mapping) else []
        mappings: list[EventEntityMapping] = []
        if isinstance(mapping_raw, list):
            for item in mapping_raw:
                if not isinstance(item, Mapping):
                    continue
                mappings.append(
                    EventEntityMapping(
                        event_id=str(item.get("event_id") or "").strip(),
                        primary_entity=str(item.get("primary_entity") or "").strip(),
                        secondary_entities=[
                            str(entity).strip()
                            for entity in (item.get("secondary_entities") or [])
                            if str(entity).strip()
                        ],
                        confidence=_clamp_confidence(item.get("confidence")),
                        needs_review=bool(item.get("needs_review", False)),
                        review_reason=str(item.get("review_reason") or "").strip(),
                    )
                )

        warnings_raw = payload.get("cross_entity_warning") if isinstance(payload, Mapping) else []
        warnings = [str(item).strip() for item in (warnings_raw or []) if str(item).strip()]

        return cls(
            date=str(payload.get("date") or "").strip(),
            entities=entities,
            event_entity_map=mappings,
            cross_entity_warning=warnings,
        )


class EntityExtractorLLM:
    def __init__(self, model_gateway: ModelGateway):
        self._model_gateway = model_gateway

    def extract_entities(self, *, date: str, events: list[dict[str, Any]]) -> EntityExtractionResult:
        spec = load_prompt("L1_entity_extractor")
        payload = {
            "date": date,
            "events": [_compact_event_for_prompt(event) for event in events],
        }
        prompt = build_prompt(spec.body, "L1_entity_extractor", payload)

        raw_response: Any = self._model_gateway.call(
            "L1_entity_extractor",
            prompt,
            input_data=payload,
        )
        if not isinstance(raw_response, dict):
            raise ValueError(f"L1_entity_extractor returned non-dict: {type(raw_response)}")

        result = EntityExtractionResult.from_dict(raw_response)
        _ensure_event_mapping_coverage(input_events=events, result=result)
        return result


def extract_entities(events: list[dict[str, Any]]) -> EntityExtractionResult:
    """Main API: run L1 extraction for a list of raw-event-like dicts."""
    scope_date = _infer_scope_date(events)
    gateway = _load_gateway()
    extractor = EntityExtractorLLM(gateway)
    return extractor.extract_entities(date=scope_date, events=events)


def extract_for_date(date: str) -> EntityExtractionResult:
    """Load raw_events for date -> cap -> run L1 extractor."""
    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    since, until = local_day_bounds(date)
    rows = query_raw_events(since=since, until=until, limit=50000)
    rows = sorted(rows, key=lambda item: (str(item.get("ts_start") or ""), int(item.get("id") or 0)))

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            event = _normalize_raw_event(row)
            event["_flagship_score"] = _flagship_event_score(event)
            events.append(event)
        except ValueError:
            continue

    capped_events, _ = cap_events_by_source(events, limit=60)

    gateway = _load_gateway()
    extractor = EntityExtractorLLM(gateway)
    return extractor.extract_entities(date=date, events=capped_events)


def _load_gateway() -> ModelGateway:
    cfg = Config.load()
    return load_model_gateway(cfg)


def _parse_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_raw_event(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _parse_metadata(row)
    entities_raw = metadata.get("entities")
    entities = dict(entities_raw) if isinstance(entities_raw, dict) else {}

    event_id = str(row.get("id") or "").strip()
    if not event_id:
        raise ValueError("raw event row missing id")

    ts_start = str(row.get("ts_start") or "").strip()
    if not ts_start:
        raise ValueError(f"raw event {event_id} missing ts_start")

    app_name = str(row.get("app_name") or metadata.get("app_name") or "unknown").strip() or "unknown"
    payload = dict(row)
    payload.update(
        {
            "id": event_id,
            "ts_start": ts_start,
            "source": str(row.get("source") or "").strip(),
            "event_type": str(row.get("event_type") or "").strip(),
            "speaker": str(row.get("speaker") or "").strip(),
            "app_name": app_name,
            "window_title": str(row.get("window_title") or metadata.get("window_title") or "").strip(),
            "process_name": str(row.get("process_name") or "").strip(),
            "content_text": str(row.get("content_text") or "").strip(),
            "ts_end": row.get("ts_end"),
            "content_hash": row.get("content_hash"),
            "session_id": str(row.get("session_id") or entities.get("session_id") or "").strip(),
            "semantic_weight": row.get("semantic_weight"),
            "user_present": row.get("user_present"),
            "metadata_json": json.dumps({**metadata, "entities": entities}, ensure_ascii=False),
        }
    )
    return payload


def _to_local_hhmm(ts_iso: str) -> str:
    text = str(ts_iso or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone(local_timezone()).strftime("%H:%M")


def _file_paths(event: Mapping[str, Any]) -> list[str]:
    metadata = _parse_metadata(event)
    entities = metadata.get("entities") if isinstance(metadata.get("entities"), dict) else {}
    raw = entities.get("file_paths") if isinstance(entities, dict) else []
    if not isinstance(raw, list):
        return []
    return [str(path).strip() for path in raw if str(path).strip()]


def _compact_event_for_prompt(event: Mapping[str, Any]) -> dict[str, Any]:
    content = str(event.get("content_text") or "").strip()
    if len(content) > 800:
        content = content[:800]

    return {
        "eid": str(event.get("id") or "").strip(),
        "t": _to_local_hhmm(str(event.get("ts_start") or "")),
        "a": str(event.get("app_name") or "").strip(),
        "c": content,
        "sp": str(event.get("speaker") or "").strip(),
        "win": str(event.get("window_title") or "").strip(),
        "wu": extract_work_unit(event),
        "fp": _file_paths(event),
    }


def _infer_scope_date(events: list[dict[str, Any]]) -> str:
    for event in events:
        ts = str(event.get("ts_start") or "").strip()
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt.astimezone(local_timezone()).date().isoformat()
    return datetime.now(tz=local_timezone()).date().isoformat()


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0:
        return 0.0
    if numeric > 1:
        return 1.0
    return numeric


def _ensure_event_mapping_coverage(*, input_events: list[dict[str, Any]], result: EntityExtractionResult) -> None:
    expected_ids = {str(event.get("id") or "").strip() for event in input_events if str(event.get("id") or "").strip()}
    mapped_ids = {mapping.event_id for mapping in result.event_entity_map if mapping.event_id}
    missing = sorted(expected_ids - mapped_ids)
    if missing:
        sample = ", ".join(missing[:5])
        raise ValueError(f"L1_entity_extractor missing event mappings for {len(missing)} events: {sample}")


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m keypulse.pipeline.entity_extractor_llm YYYY-MM-DD", file=sys.stderr)
        return 2

    result = extract_for_date(argv[1])
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
