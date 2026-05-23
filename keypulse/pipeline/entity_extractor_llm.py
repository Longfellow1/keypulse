"""Entity Extractor — LLM-based project/product identification from daily events.

设计文档: M1-C Step 2-1 — independent entity extraction module
输入: 原始 daily events（最多 60 条）
输出: 实体列表 + 事件-实体映射 + 置信度标记
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.daily_strategy import build_prompt
from keypulse.prompts.loader import load_prompt


@dataclass
class Entity:
    """识别出的项目/产品/系统实体"""

    name: str
    type: str  # project | product | tool | feature | system | other
    aliases: list[str]
    confidence: float
    evidence_event_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "aliases": self.aliases,
            "confidence": self.confidence,
            "evidence_event_ids": self.evidence_event_ids,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entity:
        return cls(
            name=str(d.get("name", "")),
            type=str(d.get("type", "other")),
            aliases=list(d.get("aliases") or []),
            confidence=float(d.get("confidence", 0.0)),
            evidence_event_ids=list(d.get("evidence_event_ids") or []),
        )


@dataclass
class EventEntityMapping:
    """单条事件的实体映射"""

    event_id: str
    primary_entity: str
    secondary_entities: list[str]
    confidence: float
    needs_review: bool
    review_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "primary_entity": self.primary_entity,
            "secondary_entities": self.secondary_entities,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EventEntityMapping:
        return cls(
            event_id=str(d.get("event_id", "")),
            primary_entity=str(d.get("primary_entity", "")),
            secondary_entities=list(d.get("secondary_entities") or []),
            confidence=float(d.get("confidence", 0.0)),
            needs_review=bool(d.get("needs_review", False)),
            review_reason=str(d.get("review_reason", "")),
        )


@dataclass
class EntityExtractionResult:
    """单日的实体抽取结果"""

    date: str
    entities: list[Entity]
    event_entity_map: list[EventEntityMapping]
    cross_entity_warning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "entities": [e.to_dict() for e in self.entities],
            "event_entity_map": [m.to_dict() for m in self.event_entity_map],
            "cross_entity_warning": self.cross_entity_warning,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntityExtractionResult:
        entities = [
            Entity.from_dict(e)
            for e in (d.get("entities") or [])
            if isinstance(e, dict)
        ]
        event_map = [
            EventEntityMapping.from_dict(m)
            for m in (d.get("event_entity_map") or [])
            if isinstance(m, dict)
        ]
        return cls(
            date=str(d.get("date", "")),
            entities=entities,
            event_entity_map=event_map,
            cross_entity_warning=list(d.get("cross_entity_warning") or []),
        )


class EntityExtractor:
    """LLM-based entity extractor for daily events"""

    def __init__(self, model_gateway: ModelGateway):
        self._model_gateway = model_gateway

    def extract_entities(self, events: list[dict[str, Any]]) -> EntityExtractionResult:
        """Extract entities from a list of events.

        Args:
            events: List of event dicts with fields like eid, t, a, c, sp, win, wu, etc.

        Returns:
            EntityExtractionResult with entities and event mappings
        """
        # Try to extract date from events or use today
        date_str = datetime.now().strftime("%Y-%m-%d")
        for event in events:
            if "date" in event:
                date_str = str(event["date"])
                break

        # Cap events to 60
        capped_events = events[:60]

        # Simplify events for LLM input (remove internal cluster metadata)
        simplified_events = []
        for event in capped_events:
            # Keep only the fields relevant for entity extraction
            simplified = {
                "eid": event.get("eid") or event.get("cluster_id"),
                "t": event.get("t", ""),
                "a": event.get("a", ""),
                "c": event.get("c") or event.get("display_name", ""),
                "sp": event.get("sp", "user"),
                "sid": event.get("sid", ""),
                "win": event.get("win", ""),
                "wu": event.get("wu") or event.get("display_name", ""),
                "fp": event.get("fp", []),
                "url": event.get("url", ""),
                "code_symbols": event.get("code_symbols", []),
            }
            simplified_events.append(simplified)

        # Build input payload
        input_data = {
            "date": date_str,
            "events": simplified_events,
        }

        # Load prompt spec
        spec = load_prompt("L1_entity_extractor")
        prompt = build_prompt(spec.body, "L1_entity_extractor", input_data)

        # Call LLM
        raw_response: Any = None
        try:
            raw_response = self._model_gateway.call(
                "L1_entity_extractor",
                prompt,
                input_data=input_data,
            )
        except Exception as exc:
            raise RuntimeError(
                f"L1_entity_extractor call failed for {date_str}: {exc}"
            ) from exc

        if not isinstance(raw_response, dict):
            raise ValueError(
                f"L1_entity_extractor returned non-dict for {date_str}: {type(raw_response)}"
            )

        return EntityExtractionResult.from_dict(raw_response)

    def extract_for_date(self, date_str: str) -> EntityExtractionResult:
        """Extract entities from a specific date's events.

        Args:
            date_str: ISO 8601 date string (YYYY-MM-DD)

        Returns:
            EntityExtractionResult
        """
        # Read events from ~/.keypulse/daily-summary/{date}.json
        daily_dir = Path.home() / ".keypulse" / "daily-summary"
        daily_file = daily_dir / f"{date_str}.json"

        if not daily_file.exists():
            raise FileNotFoundError(f"Daily summary not found: {daily_file}")

        try:
            daily_data = json.loads(daily_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to read {daily_file}: {exc}") from exc

        # Extract raw events (if available) or use clusters as events
        events = daily_data.get("events") or []

        # If no raw events but clusters exist, use clusters as event-like structures
        if not events and "clusters" in daily_data:
            clusters = daily_data.get("clusters") or []
            events = [
                {
                    "eid": f"cluster_{i}",
                    "c": cluster.get("display_name", ""),
                    "wu": cluster.get("display_name", ""),
                }
                for i, cluster in enumerate(clusters)
            ]

        # Add date if not present
        if events and "date" not in events[0]:
            for event in events:
                event["date"] = date_str

        return self.extract_entities(events)


if __name__ == "__main__":
    import sys
    from keypulse.pipeline.model import ModelGateway
    from keypulse.config import Config

    if len(sys.argv) < 2:
        print("Usage: python -m keypulse.pipeline.entity_extractor_llm <date>")
        print("Example: python -m keypulse.pipeline.entity_extractor_llm 2026-05-19")
        sys.exit(1)

    target_date = sys.argv[1]

    # Load config and initialize gateway
    config = Config()
    gateway = ModelGateway(config)

    # Run extraction
    extractor = EntityExtractor(gateway)
    result = extractor.extract_for_date(target_date)

    # Print result as formatted JSON
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
