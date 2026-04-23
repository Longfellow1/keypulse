from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from keypulse.obsidian.layout import slugify
from keypulse.utils.dates import local_timezone


HIGH_SENSITIVITY_PLACEHOLDER = "<高敏内容 · 已记录未展示>"
_INVALID_TS = datetime.max.replace(tzinfo=timezone.utc)


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return _INVALID_TS
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return _INVALID_TS


def _fmt_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    return f"{hours}h{mins % 60:02d}m"


def _format_local_datetime(dt: datetime) -> str:
    local_dt = dt.astimezone(local_timezone())
    return f"{local_dt.year}年{local_dt.month}月{local_dt.day}日 {local_dt:%H:%M}"


def _localize_ts(value: str | None) -> datetime | None:
    parsed = _parse_ts(value)
    if parsed == _INVALID_TS:
        return None
    return parsed.astimezone(local_timezone())


def _fmt_range(ts_start: str, ts_end: str) -> str:
    start = _localize_ts(ts_start)
    end = _localize_ts(ts_end)
    if start is None:
        return "—"
    start_text = _format_local_datetime(start)
    if end is None or end <= start:
        return start_text
    if end.date() == start.date():
        return f"{start_text}–{end:%H:%M}"
    return f"{start_text}–{_format_local_datetime(end)}"


def format_work_block_for_prompt(block: "WorkBlock") -> dict[str, Any]:
    payload = asdict(block)
    start = _localize_ts(payload.get("ts_start"))
    end = _localize_ts(payload.get("ts_end"))
    if start is None:
        return payload
    payload["ts_start"] = _format_local_datetime(start)
    if end is None or end <= start:
        payload["ts_end"] = payload["ts_start"]
    elif end.date() == start.date():
        payload["ts_end"] = f"{end:%H:%M}"
    else:
        payload["ts_end"] = _format_local_datetime(end)
    return payload


def _split_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _extract_tags(event: dict[str, Any]) -> list[str]:
    direct = _split_tags(event.get("tags"))
    if direct:
        return direct
    metadata_json = event.get("metadata_json")
    if not metadata_json:
        return []
    try:
        metadata = json.loads(str(metadata_json))
    except Exception:
        return []
    return _split_tags(metadata.get("tags"))


def _topic_key_for_event(event: dict[str, Any]) -> str:
    topic_key = str(event.get("topic_key") or "").strip()
    if topic_key:
        return topic_key
    tags = _extract_tags(event)
    if tags:
        return slugify("-".join(tags), fallback="topic")
    title = " ".join(
        str(part or "").strip()
        for part in (
            event.get("title"),
            event.get("window_title"),
            event.get("body"),
            event.get("content_text"),
            event.get("app_name"),
        )
        if str(part or "").strip()
    )
    return slugify(title[:80] or "topic", fallback="topic")


def _event_title(event: dict[str, Any]) -> str:
    title = str(event.get("title") or event.get("window_title") or event.get("app_name") or "").strip()
    if title:
        return title
    body = str(event.get("body") or event.get("content_text") or "").strip()
    if body:
        return " ".join(body.split())[:72]
    return str(event.get("event_type") or "event")


def _event_source(event: dict[str, Any]) -> str:
    return str(event.get("origin_source") or event.get("source") or "")


_USER_SOURCES_FOR_BLOCK = frozenset({"keyboard_chunk", "clipboard", "manual", "browser"})


def _event_speaker(event: dict[str, Any]) -> str:
    speaker = str(event.get("speaker") or "").strip()
    if speaker in {"user", "system"}:
        return speaker
    return "user" if event.get("source") in _USER_SOURCES_FOR_BLOCK else "system"


def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(event)
    try:
        sensitivity_level = int(sanitized.get("sensitivity_level") or 0)
    except Exception:
        sensitivity_level = 0
    if sensitivity_level >= 2:
        for field in ("content_text", "evidence", "title", "window_title", "body"):
            if field in sanitized:
                sanitized[field] = HIGH_SENSITIVITY_PLACEHOLDER
    return sanitized


def _importance(event: dict[str, Any]) -> float:
    source = _event_source(event)
    if source == "manual":
        return 1.0
    if source == "clipboard":
        return 0.85
    if source == "window":
        return 0.65
    return 0.5


def _summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": _event_title(event),
        "body": str(event.get("body") or event.get("content_text") or "").strip(),
        "source": _event_source(event),
        "event_type": str(event.get("event_type") or ""),
        "created_at": str(event.get("created_at") or event.get("ts_start") or ""),
        "topic_key": _topic_key_for_event(event),
        "app_name": str(event.get("app_name") or ""),
        "window_title": str(event.get("window_title") or ""),
    }


def _pick_primary_app(events: list[dict[str, Any]], fallback: str = "") -> str:
    counts = Counter(str(event.get("app_name") or event.get("window_title") or "").strip() for event in events)
    counts.pop("", None)
    if not counts:
        return fallback
    return counts.most_common(1)[0][0]


_PLACEHOLDER_TOPICS = {"topic", "event", "碎片", ""}


def _pick_display_theme_from_events(events: list[dict[str, Any]]) -> str:
    title_counts: Counter[str] = Counter()
    for event in events:
        title = str(event.get("window_title") or event.get("title") or "").strip()
        if not title:
            continue
        trimmed = " ".join(title.split())[:40]
        title_counts[trimmed] += 1
    if title_counts:
        return title_counts.most_common(1)[0][0]
    return ""


def _pick_primary_topic(events: list[dict[str, Any]], speaker_filter: str | None = None) -> str:
    filtered_events = [event for event in events if speaker_filter is None or _event_speaker(event) == speaker_filter]
    counts = Counter(_topic_key_for_event(event) for event in filtered_events)
    top = counts.most_common(1)[0][0] if counts else ""
    if top and top not in _PLACEHOLDER_TOPICS:
        return top
    display = _pick_display_theme_from_events(filtered_events or events)
    return display or top or "碎片"


def _event_sort_key(event: dict[str, Any]) -> tuple:
    return (
        _parse_ts(str(event.get("ts_start") or event.get("created_at") or "")),
        -_importance(event),
        _event_title(event).lower(),
    )


@dataclass(frozen=True)
class WorkBlock:
    theme: str
    duration_sec: int
    ts_start: str
    ts_end: str
    primary_app: str
    event_count: int
    key_candidates: list[dict[str, Any]]
    continuity: str
    user_candidates: list[dict[str, Any]] = field(default_factory=list)
    system_candidates: list[dict[str, Any]] = field(default_factory=list)
    subtopics: tuple[str, ...] = ()
    session_id: str | None = None
    fragment: bool = False


def _session_duration(session_by_id: Mapping[str, dict[str, Any]] | None, session_id: str | None, events: list[dict[str, Any]]) -> int:
    if session_by_id and session_id and session_id in session_by_id:
        value = session_by_id[session_id].get("duration_sec")
        if value is not None:
            try:
                return max(int(value), 0)
            except Exception:
                pass
    if not events:
        return 0
    start = _parse_ts(str(events[0].get("ts_start") or events[0].get("created_at") or ""))
    end = _parse_ts(str(events[-1].get("ts_end") or events[-1].get("ts_start") or events[-1].get("created_at") or ""))
    if end > start and start != datetime.max.replace(tzinfo=timezone.utc):
        return int((end - start).total_seconds())
    return 0


def _event_primary_app(event: dict[str, Any]) -> str:
    return str(event.get("app_name") or event.get("window_title") or "").strip()


def _event_gap_seconds(previous: dict[str, Any], current: dict[str, Any]) -> int:
    previous_end = _parse_ts(str(previous.get("ts_end") or previous.get("ts_start") or previous.get("created_at") or ""))
    current_start = _parse_ts(str(current.get("ts_start") or current.get("created_at") or ""))
    if previous_end == datetime.max.replace(tzinfo=timezone.utc) or current_start == datetime.max.replace(tzinfo=timezone.utc):
        return 0
    return max(int((current_start - previous_end).total_seconds()), 0)


def _block_duration_seconds(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    starts = [
        _parse_ts(str(event.get("ts_start") or event.get("created_at") or ""))
        for event in events
    ]
    ends = [
        _parse_ts(str(event.get("ts_end") or event.get("ts_start") or event.get("created_at") or ""))
        for event in events
    ]
    start = min(starts, default=datetime.max.replace(tzinfo=timezone.utc))
    end = max(ends, default=datetime.max.replace(tzinfo=timezone.utc))
    if start == datetime.max.replace(tzinfo=timezone.utc) or end <= start:
        return 0
    return int((end - start).total_seconds())


def _block_subtopics(events: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_topic_key_for_event(event) for event in events))


def _build_work_block(
    events: list[dict[str, Any]],
    *,
    session_id: str | None,
    session_by_id: Mapping[str, dict[str, Any]] | None,
    recent_topic_keys: set[str] | None,
    previous_day_topic_keys: set[str] | None,
) -> WorkBlock:
    ts_start = str(events[0].get("ts_start") or events[0].get("created_at") or "")
    ts_end = str(events[-1].get("ts_end") or events[-1].get("ts_start") or events[-1].get("created_at") or "")
    duration_sec = _block_duration_seconds(events)
    if duration_sec <= 0:
        duration_sec = _session_duration(session_by_id, session_id, events)
    user_events = [event for event in events if _event_speaker(event) == "user"]
    system_events = [event for event in events if _event_speaker(event) == "system"]
    theme = _pick_primary_topic(user_events, speaker_filter="user") if user_events else ""
    if not theme or theme in _PLACEHOLDER_TOPICS:
        theme = _pick_primary_topic(events)
    primary_app = _pick_primary_app(events, fallback=_event_primary_app(events[0]))
    fragment = duration_sec < 300
    if not user_events:
        fragment = True
    return WorkBlock(
        theme=theme,
        duration_sec=duration_sec,
        ts_start=ts_start,
        ts_end=ts_end,
        primary_app=primary_app,
        event_count=len(events),
        key_candidates=[_summarize_event(event) for event in sorted(events, key=lambda event: (-_importance(event), _event_title(event).lower()))[:3]],
        continuity=(
            "continued"
            if previous_day_topic_keys and theme in previous_day_topic_keys
            else "returned"
            if recent_topic_keys and theme in recent_topic_keys
            else "new"
        ),
        user_candidates=[_summarize_event(event) for event in sorted(user_events, key=lambda ev: (-_importance(ev), _event_title(ev).lower()))[:3]],
        system_candidates=[_summarize_event(event) for event in sorted(system_events, key=lambda ev: (-_importance(ev), _event_title(ev).lower()))[:3]],
        subtopics=_block_subtopics(events),
        session_id=session_id,
        fragment=fragment,
    )


def _render_block_lines(block: WorkBlock, evidence_formatter: Callable[[dict[str, Any]], str] | None) -> list[str]:
    duration = _fmt_duration(block.duration_sec)
    time_range = _fmt_range(block.ts_start, block.ts_end)
    user_candidates = block.user_candidates or []
    system_candidates = block.system_candidates or []
    formatter = evidence_formatter or (lambda item: item["title"])
    lines = [f"### {block.theme} · {duration}（{time_range}）", "", "**你做了什么**", ""]

    if user_candidates:
        lines.extend(_render_candidate_lines(user_candidates, formatter))
    else:
        lines.append("（这段时间没有键入或剪贴板记录）")

    if system_candidates:
        lines.extend(["", f"<details>", f"<summary>系统显示了什么（{len(system_candidates)} 条）</summary>", ""])
        lines.extend(_render_candidate_lines(system_candidates, formatter))
        lines.extend(["", "</details>"])
    return lines


def aggregate_work_blocks(
    events: list[dict[str, Any]] | None,
    *,
    sessions: list[dict[str, Any]] | None = None,
    recent_topic_keys: set[str] | None = None,
    previous_day_topic_keys: set[str] | None = None,
) -> list[WorkBlock]:
    normalized = [_sanitize_event(event) for event in (events or []) if event is not None]
    normalized.sort(key=_event_sort_key)

    session_by_id = {str(session.get("id") or ""): session for session in (sessions or []) if str(session.get("id") or "").strip()}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, event in enumerate(normalized):
        session_id = str(event.get("session_id") or "").strip()
        if session_id:
            group_key = session_id
        else:
            app = _event_primary_app(event)
            group_key = f"app:{app}" if app else f"event:{index}"
        grouped[group_key].append(event)

    blocks: list[WorkBlock] = []
    for session_key, session_events in grouped.items():
        session_events.sort(key=_event_sort_key)
        session_id = str(session_events[0].get("session_id") or "").strip() or None
        session_runs: list[list[dict[str, Any]]] = []
        current_run: list[dict[str, Any]] = []
        current_app = ""
        for event in session_events:
            event_app = _event_primary_app(event)
            if current_run and event_app == current_app and _event_gap_seconds(current_run[-1], event) <= 300:
                current_run.append(event)
                continue
            if current_run:
                session_runs.append(current_run)
            current_run = [event]
            current_app = event_app
        if current_run:
            session_runs.append(current_run)

        for run_events in session_runs:
            blocks.append(
                _build_work_block(
                    run_events,
                    session_id=session_id,
                    session_by_id=session_by_id,
                    recent_topic_keys=recent_topic_keys,
                    previous_day_topic_keys=previous_day_topic_keys,
                )
            )

    blocks.sort(key=lambda block: _parse_ts(block.ts_start))

    return blocks


def _render_evidence(block: WorkBlock, evidence_formatter: Callable[[dict[str, Any]], str] | None) -> str:
    candidates = block.key_candidates[:2]
    if not candidates:
        return ""
    formatter = evidence_formatter or (lambda item: item["title"])
    rendered = []
    for candidate in candidates:
        text = formatter(candidate)
        if text:
            rendered.append(text)
    return " · ".join(rendered)


def _render_candidate_lines(candidates: list[dict[str, Any]], formatter: Callable[[dict[str, Any]], str]) -> list[str]:
    lines: list[str] = []
    for candidate in candidates:
        text = formatter(candidate)
        if text:
            lines.append(f"- {text}")
    return lines


def render_daily_narrative(
    work_blocks: Iterable[WorkBlock],
    *,
    evidence_formatter: Callable[[dict[str, Any]], str] | None = None,
    include_heading: bool = True,
) -> str:
    blocks = [block for block in work_blocks]
    if not blocks:
        return "## 今日主线\n\n今天没有形成足够清晰的工作块。" if include_heading else "今天没有形成足够清晰的工作块。"

    lead_block = max((block for block in blocks if not block.fragment), default=blocks[0], key=lambda block: block.duration_sec)
    lines = ["## 今日主线", "", f"> 主战场是 {lead_block.theme}。", ""] if include_heading else [f"> 主战场是 {lead_block.theme}。", ""]
    non_fragments = [block for block in blocks if not block.fragment]
    fragments = [block for block in blocks if block.fragment]
    for block in non_fragments:
        lines.extend(_render_block_lines(block, evidence_formatter))
        lines.append("")
    if fragments:
        lines.extend(
            [
                f"### 碎片汇总 · {_fmt_duration(sum(block.duration_sec for block in fragments))}",
                "",
                f"> 另有 {len(fragments)} 个零散片段，共 {sum(block.event_count for block in fragments)} 条事件，见附录。",
                "",
            ]
        )

    return "\n".join(lines).strip()
