from __future__ import annotations

import hashlib
import json
from datetime import datetime

from keypulse.pipeline.entity_extractor import Entity, extract
from keypulse.pipeline.event_assigner import assign_events
from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.outline_prompt import request_outline
from keypulse.pipeline.session_splitter import split_into_sessions
from keypulse.pipeline.thing_clusterer import Thing, cluster
from keypulse.pipeline.thing_renderer import render_thing
from keypulse.sources.registry import read_all
from keypulse.sources.types import SemanticEvent


def build_things(
    since: datetime,
    until: datetime,
    *,
    model_gateway: ModelGateway | None = None,
    sources: list[str] | None = None,
    idle_threshold_minutes: int = 30,
) -> list[Thing]:
    events = _read_events(since, until, sources=sources)
    if not events:
        return []

    sessions = split_into_sessions(events, idle_threshold_minutes=idle_threshold_minutes)
    things: list[Thing] = []

    for session in sessions:
        outlines = request_outline(session, model_gateway=model_gateway)
        assigned = assign_events(session.events, outlines)
        for title, event_list in assigned.items():
            if not event_list:
                continue
            ordered = sorted(event_list, key=lambda event: event.time)
            things.append(
                Thing(
                    id=_stable_thing_id(title, ordered),
                    title=title,
                    entities=collect_entities(ordered),
                    events=ordered,
                    time_start=ordered[0].time,
                    time_end=ordered[-1].time,
                    sources={event.source for event in ordered},
                )
            )

    things.sort(key=lambda thing: thing.time_start)
    return things


def render_things_report(
    things: list[Thing],
    *,
    model_gateway: ModelGateway | None = None,
    title: str = "今日做的事",
) -> str:
    if not things:
        return f"# {title}\n\n（无事件）"

    body = [f"# {title}"]
    for thing in things:
        body.append("")
        body.append(render_thing(thing, model_gateway=model_gateway))
    return "\n".join(body).strip() + "\n"


def things_as_json(things: list[Thing]) -> str:
    payload = []
    for thing in things:
        payload.append(
            {
                "id": thing.id,
                "title": thing.title,
                "entities": [entity.__dict__ for entity in thing.entities],
                "events": [
                    {
                        "time": event.time.isoformat(),
                        "source": event.source,
                        "actor": event.actor,
                        "intent": event.intent,
                        "artifact": event.artifact,
                        "raw_ref": event.raw_ref,
                        "privacy_tier": event.privacy_tier,
                        "metadata": event.metadata,
                    }
                    for event in thing.events
                ],
                "time_start": thing.time_start.isoformat(),
                "time_end": thing.time_end.isoformat(),
                "sources": sorted(thing.sources),
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def collect_entities(events: list[SemanticEvent]) -> list[Entity]:
    merged: dict[tuple[str, str], Entity] = {}
    for event in events:
        for entity in extract(event):
            key = (entity.kind, entity.value)
            current = merged.get(key)
            if current is None or entity.confidence > current.confidence:
                merged[key] = entity
    return list(merged.values())


def _read_events(
    since: datetime,
    until: datetime,
    *,
    sources: list[str] | None,
) -> list[SemanticEvent]:
    events: list[SemanticEvent] = []
    if sources:
        for source_name in sources:
            events.extend(list(read_all(since, until, source=source_name)))
    else:
        events.extend(list(read_all(since, until, source=None)))
    return sorted(events, key=lambda event: event.time)


def _stable_thing_id(title: str, events: list[SemanticEvent]) -> str:
    basis = "|".join(
        [
            title,
            *[
                f"{event.time.isoformat()}:{event.source}:{event.intent}:{event.artifact}"
                for event in events
            ],
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def build_things_legacy(events: list[SemanticEvent]) -> list[Thing]:
    """Legacy entity-cooccurrence clustering path kept for compatibility."""
    return cluster(events)
