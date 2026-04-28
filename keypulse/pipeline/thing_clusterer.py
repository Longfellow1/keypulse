# DEPRECATED: replaced by session_splitter + LLM outline in S2.9.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from keypulse.pipeline.entity_extractor import Entity, extract
from keypulse.sources.types import SemanticEvent


@dataclass
class Thing:
    id: str
    title: str
    entities: list[Entity]
    events: list[SemanticEvent]
    time_start: datetime
    time_end: datetime
    sources: set[str]


def cluster(
    events: list[SemanticEvent],
    *,
    time_window_minutes: int = 30,
    threshold: float = 0.3,
) -> list[Thing]:
    if not events:
        return []

    ordered = sorted(events, key=lambda event: event.time)
    states: list[dict] = []

    for event in ordered:
        event_entities = extract(event)
        target = _find_target_state(
            states,
            event,
            event_entities,
            time_window=timedelta(minutes=time_window_minutes),
            threshold=threshold,
        )
        if target is None:
            states.append(
                {
                    "events": [event],
                    "entities": list(event_entities),
                    "time_start": event.time,
                    "time_end": event.time,
                    "sources": {event.source},
                }
            )
            continue

        target["events"].append(event)
        target["time_end"] = max(target["time_end"], event.time)
        target["sources"].add(event.source)
        existing = {(entity.kind, entity.value): entity for entity in target["entities"]}
        for entity in event_entities:
            key = (entity.kind, entity.value)
            current = existing.get(key)
            if current is None or entity.confidence > current.confidence:
                existing[key] = entity
        target["entities"] = list(existing.values())

    things: list[Thing] = []
    for state in states:
        state_events = sorted(state["events"], key=lambda event: event.time)
        state_entities = list(state["entities"])
        title = _safe_title(_build_title(state_entities, state["time_start"], state["time_end"]))
        identity = _stable_id(state_events)
        things.append(
            Thing(
                id=identity,
                title=title,
                entities=state_entities,
                events=state_events,
                time_start=state["time_start"],
                time_end=state["time_end"],
                sources=set(state["sources"]),
            )
        )

    things.sort(key=lambda thing: thing.time_start)
    return things


def _find_target_state(states: list[dict], event: SemanticEvent, entities: list[Entity], *, time_window: timedelta, threshold: float):
    commit_values = {entity.value for entity in entities if entity.kind == "commit"}
    session_values = {entity.value for entity in entities if entity.kind == "session"}
    file_values = {entity.value for entity in entities if entity.kind == "file"}
    entity_keys = {(entity.kind, entity.value) for entity in entities}

    best = None
    best_score = -1.0
    for state in states:
        state_keys = {(entity.kind, entity.value) for entity in state["entities"]}
        state_commits = {value for kind, value in state_keys if kind == "commit"}
        state_sessions = {value for kind, value in state_keys if kind == "session"}
        state_files = {value for kind, value in state_keys if kind == "file"}

        if commit_values & state_commits:
            return state
        if session_values & state_sessions:
            return state

        close_in_time = abs((event.time - state["time_end"]).total_seconds()) <= time_window.total_seconds()
        if close_in_time and file_values & state_files:
            return state

        if not close_in_time:
            continue

        similarity = _jaccard(entity_keys, state_keys)
        if similarity >= threshold and similarity > best_score:
            best = state
            best_score = similarity

    return best


def _jaccard(a: set[tuple[str, str]], b: set[tuple[str, str]]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _build_title(entities: list[Entity], start: datetime, end: datetime) -> str:
    if entities:
        picks = sorted(entities, key=lambda entity: entity.confidence, reverse=True)[:2]
        label = " / ".join(entity.value for entity in picks)
    else:
        label = "未命名事项"
    return f"{label} ({start.strftime('%H:%M')}-{end.strftime('%H:%M')})"


def _safe_title(title: str) -> str:
    cleaned = " ".join(title.replace("\r", " ").replace("\n", " ").split())
    return cleaned.replace("#", "")[:200]


def _stable_id(events: list[SemanticEvent]) -> str:
    basis = "|".join(f"{event.time.isoformat()}:{event.source}:{event.intent}:{event.artifact}" for event in events)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
