from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from keypulse.pipeline.entity_extractor import Entity, extract
from keypulse.pipeline.model import ModelGateway, PipelineQualityError
from keypulse.pipeline.overview import render_overview
from keypulse.pipeline.session_renderer import render_session_things
from keypulse.pipeline.session_splitter import ActivitySession, split_into_sessions
from keypulse.pipeline.thing import Thing
from keypulse.sources.registry import read_all
from keypulse.sources.types import SemanticEvent

logger = logging.getLogger(__name__)

_LOCAL_TZ = timezone(timedelta(hours=8))
_SOURCE_NAME_MAP = {
    "chrome_history": "Chrome",
    "knowledgec": "应用活动",
    "wechat": "微信",
    "git_log": "Git",
    "codex_cli": "Codex",
    "claude_code": "Claude",
    "zsh_history": "Terminal",
    "markdown_vault": "Obsidian",
    "approved_sqlite": "SQLite Reader",
    "safari_history": "Safari",
}


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
        try:
            things.extend(render_session_things(session, model_gateway=model_gateway))
        except PipelineQualityError as exc:
            logger.warning(
                "session_renderer failed (session_id=%s, %d events): %s; using fallback thing",
                session.id,
                len(session.events),
                exc,
            )
            things.append(_fallback_thing_from_session(session))

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
    overview = render_overview(things, model_gateway)
    body.append("")
    body.append("## 今日概览")
    body.append("")
    body.append(overview)

    for thing in things:
        body.append("")
        body.append(f"### {thing.title}")
        body.append(thing.narrative)

    return "\n".join(body).strip() + "\n"


def things_as_json(things: list[Thing]) -> str:
    payload = []
    for thing in things:
        payload.append(
            {
                "id": thing.id,
                "title": thing.title,
                "narrative": thing.narrative,
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


def _build_entities_from_session(session: ActivitySession) -> list[Entity]:
    entities_seen: dict[tuple[str, str], Entity] = {}
    for event in session.events:
        for entity in extract(event):
            key = (entity.kind, entity.value)
            current = entities_seen.get(key)
            if current is None or entity.confidence > current.confidence:
                entities_seen[key] = entity
    return list(entities_seen.values())


def _human_source_name(source: str) -> str:
    return _SOURCE_NAME_MAP.get(source, source)


def _fallback_thing_from_session(session: ActivitySession) -> Thing:
    source_counts = Counter(event.source for event in session.events)
    top_sources_raw = [name for name, _ in source_counts.most_common(3)]
    top_sources = [_human_source_name(name) for name in top_sources_raw]
    top_apps = "、".join(top_sources) if top_sources else "未知来源"
    local_start = session.time_start.astimezone(_LOCAL_TZ)
    local_end = session.time_end.astimezone(_LOCAL_TZ)
    title = f"{local_start:%H:%M}-{local_end:%H:%M} 在 {top_apps}"
    narrative = (
        f"这段时间共 {len(session.events)} 条事件，主要在 {top_apps} 活动；"
        "narrative 生成失败，仅显示骨架。"
    )
    thing_id = hashlib.sha256(f"{session.id}|fallback".encode()).hexdigest()[:12]
    return Thing(
        id=thing_id,
        title=title,
        entities=_build_entities_from_session(session),
        events=list(session.events),
        time_start=session.time_start,
        time_end=session.time_end,
        sources={event.source for event in session.events},
        narrative=narrative,
    )
