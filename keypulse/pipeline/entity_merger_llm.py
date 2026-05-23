"""L2 Entity Merger — cross-day canonical entity merge via LLM."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from keypulse.config import Config
from keypulse.pipeline.daily_strategy import build_prompt
from keypulse.pipeline.model import ModelGateway, load_model_gateway
from keypulse.prompts.loader import load_prompt
from keypulse.utils.dates import local_timezone


@dataclass(frozen=True)
class CanonicalEntity:
    canonical_name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    appears_on_dates: list[str] = field(default_factory=list)
    merge_reasoning: str = ""


@dataclass(frozen=True)
class MergeDecision:
    from_name: str
    to_name: str
    reason: str


@dataclass(frozen=True)
class EntityMergeResult:
    canonical_entities: list[CanonicalEntity] = field(default_factory=list)
    merge_decisions: list[MergeDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_entities": [
                {
                    "canonical_name": item.canonical_name,
                    "type": item.type,
                    "aliases": item.aliases,
                    "appears_on_dates": item.appears_on_dates,
                    "merge_reasoning": item.merge_reasoning,
                }
                for item in self.canonical_entities
            ],
            "merge_decisions": [
                {
                    "from": item.from_name,
                    "to": item.to_name,
                    "reason": item.reason,
                }
                for item in self.merge_decisions
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EntityMergeResult":
        canonical_raw = payload.get("canonical_entities") if isinstance(payload, Mapping) else []
        canonical_entities: list[CanonicalEntity] = []
        if isinstance(canonical_raw, list):
            for item in canonical_raw:
                if not isinstance(item, Mapping):
                    continue
                canonical_name = str(item.get("canonical_name") or "").strip()
                if not canonical_name:
                    continue
                canonical_entities.append(
                    CanonicalEntity(
                        canonical_name=canonical_name,
                        type=str(item.get("type") or "unknown").strip() or "unknown",
                        aliases=_to_clean_string_list(item.get("aliases")),
                        appears_on_dates=_to_clean_string_list(item.get("appears_on_dates")),
                        merge_reasoning=str(item.get("merge_reasoning") or "").strip(),
                    )
                )

        decisions_raw = payload.get("merge_decisions") if isinstance(payload, Mapping) else []
        merge_decisions: list[MergeDecision] = []
        if isinstance(decisions_raw, list):
            for item in decisions_raw:
                if not isinstance(item, Mapping):
                    continue
                from_name = str(item.get("from") or "").strip()
                to_name = str(item.get("to") or "").strip()
                if not from_name or not to_name:
                    continue
                merge_decisions.append(
                    MergeDecision(
                        from_name=from_name,
                        to_name=to_name,
                        reason=str(item.get("reason") or "").strip(),
                    )
                )
        return cls(canonical_entities=canonical_entities, merge_decisions=merge_decisions)


class EntityMergerLLM:
    def __init__(self, model_gateway: ModelGateway):
        self._model_gateway = model_gateway

    def merge_entities(
        self,
        *,
        daily_outputs: list[Mapping[str, Any]],
        evidence_events_by_date: Mapping[str, list[Mapping[str, Any]]] | None = None,
    ) -> EntityMergeResult:
        if not daily_outputs:
            raise ValueError("daily_outputs is empty")

        payload = _build_merger_payload(
            daily_outputs=daily_outputs,
            evidence_events_by_date=evidence_events_by_date or {},
        )
        spec = load_prompt("L2_entity_merger")
        prompt = build_prompt(spec.body, "L2_entity_merger", payload)

        raw_response: Any = self._model_gateway.call(
            "L2_entity_merger",
            prompt,
            input_data=payload,
            max_tokens=4000,
            temperature=0.1,
        )
        if not isinstance(raw_response, dict):
            raise ValueError(f"L2_entity_merger returned non-dict: {type(raw_response)}")

        return EntityMergeResult.from_dict(raw_response)


def merge_entities(
    daily_outputs: list[Mapping[str, Any]],
    *,
    evidence_events_by_date: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> EntityMergeResult:
    """Main API: run L2 merge for multiple days of L1 extractor outputs."""
    gateway = _load_gateway()
    merger = EntityMergerLLM(gateway)
    return merger.merge_entities(
        daily_outputs=daily_outputs,
        evidence_events_by_date=evidence_events_by_date,
    )


def _load_gateway() -> ModelGateway:
    cfg = Config.load()
    return load_model_gateway(cfg)


def _build_merger_payload(
    *,
    daily_outputs: list[Mapping[str, Any]],
    evidence_events_by_date: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, Any]:
    days_payload: list[dict[str, Any]] = []
    entity_mentions: list[dict[str, Any]] = []

    for day_payload in daily_outputs:
        if not isinstance(day_payload, Mapping):
            continue
        date = str(day_payload.get("date") or "").strip()
        if not date:
            continue

        event_lookup = _build_event_lookup(evidence_events_by_date.get(date) or [])
        entities_raw = day_payload.get("entities")
        entities_payload: list[dict[str, Any]] = []
        if isinstance(entities_raw, list):
            for entity_raw in entities_raw:
                if not isinstance(entity_raw, Mapping):
                    continue
                name = str(entity_raw.get("name") or "").strip()
                if not name:
                    continue

                aliases = _to_clean_string_list(entity_raw.get("aliases"))
                evidence_event_ids = _to_clean_string_list(entity_raw.get("evidence_event_ids"))
                entity_type = str(entity_raw.get("type") or "unknown").strip() or "unknown"
                evidence_context = _resolve_evidence_context(evidence_event_ids, event_lookup)

                entity_item = {
                    "name": name,
                    "type": entity_type,
                    "aliases": aliases,
                    "evidence_event_ids": evidence_event_ids,
                    "evidence_event_count": len(evidence_event_ids),
                    "evidence_context": evidence_context,
                }
                entities_payload.append(entity_item)
                entity_mentions.append(
                    {
                        "date": date,
                        "name": name,
                        "type": entity_type,
                        "aliases": aliases,
                        "evidence_event_ids": evidence_event_ids,
                        "evidence_event_count": len(evidence_event_ids),
                    }
                )

        days_payload.append({"date": date, "entities": entities_payload})

    return {
        "day_count": len(days_payload),
        "entity_count": len(entity_mentions),
        "days": days_payload,
        "entity_mentions": entity_mentions,
    }


def _build_event_lookup(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "").strip()
        if not event_id:
            continue
        lookup[event_id] = {
            "event_id": event_id,
            "time": _to_local_hhmm(str(event.get("ts_start") or "")),
            "app_name": str(event.get("app_name") or "").strip(),
            "work_unit": str(event.get("work_unit") or "").strip(),
            "window_title": str(event.get("window_title") or "").strip(),
            "content_excerpt": _truncate(str(event.get("content_text") or "").strip(), 180),
            "file_paths": _to_clean_string_list(event.get("file_paths"))[:2],
            "urls": _to_clean_string_list(event.get("urls"))[:1],
        }
    return lookup


def _resolve_evidence_context(
    evidence_event_ids: list[str],
    event_lookup: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    evidence_context: list[dict[str, Any]] = []
    for event_id in evidence_event_ids[:8]:
        context = event_lookup.get(event_id)
        if context:
            evidence_context.append(dict(context))
    if evidence_context:
        return evidence_context

    return [
        {
            "event_id": event_id,
            "time": "",
            "app_name": "",
            "work_unit": "",
            "window_title": "",
            "content_excerpt": "",
            "file_paths": [],
            "urls": [],
        }
        for event_id in evidence_event_ids[:3]
    ]


def _to_clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _to_local_hhmm(ts_iso: str) -> str:
    text = str(ts_iso or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone(local_timezone()).strftime("%H:%M")


def _default_eval_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "entity-extractor-eval-2026-05.json"


def _load_eval_days(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    days = payload.get("days")
    if not isinstance(days, list):
        raise ValueError(f"invalid eval payload: missing days list in {path}")

    daily_outputs: list[dict[str, Any]] = []
    evidence_events_by_date: dict[str, list[dict[str, Any]]] = {}

    for day in days:
        if not isinstance(day, Mapping):
            continue
        date = str(day.get("date") or "").strip()
        extractor_output = day.get("extractor_output")
        if isinstance(extractor_output, Mapping):
            normalized_output = dict(extractor_output)
            if date and not str(normalized_output.get("date") or "").strip():
                normalized_output["date"] = date
            daily_outputs.append(normalized_output)

        capped_events = day.get("capped_events")
        if date and isinstance(capped_events, list):
            evidence_events_by_date[date] = [dict(item) for item in capped_events if isinstance(item, Mapping)]

    return daily_outputs, evidence_events_by_date


def _main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("Usage: python -m keypulse.pipeline.entity_merger_llm [EVAL_JSON_PATH]", file=sys.stderr)
        return 2

    eval_path = Path(argv[1]).expanduser() if len(argv) == 2 else _default_eval_path()
    if not eval_path.exists():
        print(f"Eval file not found: {eval_path}", file=sys.stderr)
        return 2

    daily_outputs, evidence_events_by_date = _load_eval_days(eval_path)
    if not daily_outputs:
        print(f"No extractor_output entries found in {eval_path}", file=sys.stderr)
        return 1

    merger = EntityMergerLLM(_load_gateway())
    result = merger.merge_entities(
        daily_outputs=daily_outputs,
        evidence_events_by_date=evidence_events_by_date,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
