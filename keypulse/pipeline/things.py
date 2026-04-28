from __future__ import annotations

import json
from datetime import datetime

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.thing_clusterer import Thing, cluster
from keypulse.pipeline.thing_renderer import render_thing
from keypulse.sources.registry import read_all


def build_things(
    since: datetime,
    until: datetime,
    *,
    model_gateway: ModelGateway | None = None,
    sources: list[str] | None = None,
) -> list[Thing]:
    _ = model_gateway
    events = []
    if sources:
        for source_name in sources:
            events.extend(list(read_all(since, until, source=source_name)))
    else:
        events.extend(list(read_all(since, until, source=None)))

    if not events:
        return []
    events.sort(key=lambda event: event.time)
    return cluster(events)


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
