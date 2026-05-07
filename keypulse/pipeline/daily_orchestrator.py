from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from keypulse.config import Config
from keypulse.integrations import resolve_active_sink
from keypulse.pipeline.clustering import (
    build_evidence_graph,
    build_feature_index,
    component_features,
    connected_components,
    detect_merge_candidates,
)
from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.model import LLMCallError, ModelGateway, load_model_gateway
from keypulse.store.repository import query_raw_events
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.dates import local_day_bounds
from keypulse.utils.paths import get_data_dir


_TRIGGER_VALUES = {"18:00", "23:30"}
_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,29}")


class DailyOrchestratorError(RuntimeError):
    """Raised when daily orchestration cannot complete and caller should fallback."""


@dataclass(frozen=True)
class DailyCluster:
    component_id: str
    topic_action: str
    topic_slug: str | None
    display_name: str
    event_ids: tuple[str, ...]
    narrative: str
    merge_with_component: str | None = None


@dataclass(frozen=True)
class DailySummary:
    date: str
    trigger: str
    event_count: int
    processed_count: int
    cluster_count: int
    misc_event_ids: tuple[str, ...]
    topic_diffs: tuple[str, ...]
    daily_path: str
    summary_path: str
    skipped: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _extract_event_payload(row: Mapping[str, Any]) -> dict[str, Any]:
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
    content_text = str(row.get("content_text") or "").strip()
    window_title = str(row.get("window_title") or metadata.get("window_title") or "").strip()

    return {
        "id": event_id,
        "ts_start": ts_start,
        "app_name": app_name,
        "window_title": window_title,
        "content_text": content_text,
        "metadata_json": json.dumps({**metadata, "entities": entities}, ensure_ascii=False),
    }


def _build_prompt(spec_body: str, capability: str, payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join(
        [
            f"CAPABILITY: {capability}",
            spec_body.strip(),
            _INPUT_MARKER_BEGIN,
            rendered,
            _INPUT_MARKER_END,
        ]
    )


def _extract_prompt_payload(prompt: str) -> tuple[str, dict[str, Any]]:
    first_line = prompt.splitlines()[0] if prompt.splitlines() else ""
    capability = first_line.replace("CAPABILITY:", "").strip() if first_line.startswith("CAPABILITY:") else ""

    begin = prompt.find(_INPUT_MARKER_BEGIN)
    end = prompt.find(_INPUT_MARKER_END)
    if begin < 0 or end < 0 or end <= begin:
        return capability, {}
    content = prompt[begin + len(_INPUT_MARKER_BEGIN) : end].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {}
    return capability, payload if isinstance(payload, dict) else {}


def _keywords_from_text(text: str) -> list[str]:
    tokens = _WORD_RE.findall(text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _slugify_topic(text: str, *, fallback: str) -> str:
    tokens = _keywords_from_text(text)
    if not tokens:
        tokens = _keywords_from_text(fallback) or ["topic", "new"]
    slug = "-".join(tokens[:5]).strip("-")
    if not slug:
        slug = fallback
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "topic-new"
    if not slug[0].isalpha():
        slug = f"t-{slug}"
    return slug[:41].rstrip("-")


def _mock_l1_output(payload: dict[str, Any]) -> dict[str, Any]:
    components = payload.get("components") or []
    topics = payload.get("existing_topics_index") or []
    hot = set(payload.get("hot_cache") or [])
    topic_map: dict[str, dict[str, Any]] = {
        str(item.get("slug")): item for item in topics if isinstance(item, dict) and str(item.get("slug") or "").strip()
    }

    output_clusters: list[dict[str, Any]] = []
    misc_event_ids: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("component_id") or "")
        event_ids = [str(item) for item in (component.get("event_ids") or []) if str(item).strip()]
        keywords = [str(item).lower() for item in (component.get("keywords") or []) if str(item).strip()]

        if any("misc" in word for word in keywords):
            output_clusters.append(
                {
                    "component_id": component_id,
                    "topic_action": "misc",
                    "reason": "misc keyword matched",
                }
            )
            misc_event_ids.extend(event_ids)
            continue

        hit_slug = None
        best_score = -1
        for slug, topic in topic_map.items():
            topic_keywords = [str(item).lower() for item in (topic.get("keywords") or []) if str(item).strip()]
            overlap = len(set(keywords) & set(topic_keywords))
            score = overlap + (2 if slug in hot and overlap > 0 else 0)
            if score > best_score and score > 0:
                best_score = score
                hit_slug = slug

        if hit_slug:
            output_clusters.append(
                {
                    "component_id": component_id,
                    "topic_action": "existing",
                    "topic_slug": hit_slug,
                    "reason": "keyword overlap with existing topic",
                }
            )
            continue

        output_clusters.append(
            {
                "component_id": component_id,
                "topic_action": "new",
                "reason": "no existing topic overlap",
            }
        )

    return {"clusters": output_clusters, "misc_event_ids": misc_event_ids}


def _mock_l2_output(payload: dict[str, Any]) -> dict[str, str]:
    events = payload.get("events") or []
    parts: list[str] = []
    for event in events[:3]:
        if not isinstance(event, dict):
            continue
        app = str(event.get("app") or "应用")
        content = str(event.get("content") or "处理任务").strip()
        parts.append(f"在{app}里{content}")
    sentence = "，随后".join(parts) if parts else "围绕同一主线持续推进并记录了关键动作"
    text = f"今天这组事件主要是{sentence}，整体节奏连续且目标一致，形成了可回看的一条工作脉络。"
    return {"markdown": text[:260]}


def _mock_l3_output(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    existing = {str(item) for item in (payload.get("existing_slugs") or [])}
    content = " ".join(str(item.get("content") or "") for item in events if isinstance(item, dict))
    slug = _slugify_topic(content, fallback="topic-new")
    if slug in existing:
        index = 2
        candidate = f"{slug}-{index}"
        while candidate in existing:
            index += 1
            candidate = f"{slug}-{index}"
        slug = candidate

    keywords = _keywords_from_text(content)
    while len(keywords) < 5:
        keywords.append(f"kw{len(keywords)+1}")
    keywords = keywords[:10]
    return {
        "slug": slug,
        "display_name": f"{slug.replace('-', ' ').title()}",
        "keywords": keywords[:10],
    }


def _install_mock_backend(gateway: ModelGateway) -> None:
    fail_budget = int(os.environ.get("MOCK_LLM_FAILS", "0") or 0)
    fail_counter = {"count": 0}

    def fake_call_capability_backend(**kwargs):
        if fail_counter["count"] < fail_budget:
            fail_counter["count"] += 1
            raise RuntimeError("mock llm forced failure")

        prompt = str(kwargs.get("prompt") or "")
        capability, payload = _extract_prompt_payload(prompt)
        if capability == "L1_cluster_review":
            body = json.dumps(_mock_l1_output(payload), ensure_ascii=False)
        elif capability == "L2_narrative":
            body = json.dumps(_mock_l2_output(payload), ensure_ascii=False)
        elif capability == "L3_topic_naming":
            body = json.dumps(_mock_l3_output(payload), ensure_ascii=False)
        else:
            body = json.dumps({"ok": True}, ensure_ascii=False)
        return {"text": body, "in_tokens": 120, "out_tokens": 40, "cost_usd": 0.0}

    gateway._call_capability_backend = fake_call_capability_backend  # type: ignore[attr-defined,assignment]


def _load_gateway() -> ModelGateway:
    cfg = Config.load()
    gateway = load_model_gateway(cfg)
    if os.environ.get("MOCK_LLM") == "1":
        _install_mock_backend(gateway)
    return gateway


def _state_path() -> Path:
    return get_data_dir() / "daily-orchestrator-state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"dates": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"dates": {}}
    if isinstance(payload, dict):
        return payload
    return {"dates": {}}


def _write_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _load_topic_index() -> list[dict[str, Any]]:
    topics_dir = get_data_dir() / "topics"
    if not topics_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        text = topic_path.read_text(encoding="utf-8")
        slug = topic_path.stem
        display_name = slug
        keywords: list[str] = []
        last_seen = datetime.fromtimestamp(topic_path.stat().st_mtime, tz=timezone.utc).date().isoformat()

        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            idx = 1
            in_keywords = False
            while idx < len(lines):
                stripped = lines[idx].strip()
                idx += 1
                if stripped == "---":
                    break
                if stripped.startswith("display_name:"):
                    display_name = stripped.split(":", 1)[1].strip().strip('"').strip("'") or display_name
                elif stripped.startswith("last_seen:"):
                    value = stripped.split(":", 1)[1].strip()
                    if value:
                        last_seen = value
                elif stripped.startswith("keywords:"):
                    inline = stripped.split(":", 1)[1].strip()
                    if inline.startswith("[") and inline.endswith("]"):
                        for item in inline.strip("[]").split(","):
                            keyword = item.strip().strip('"').strip("'").lower()
                            if keyword:
                                keywords.append(keyword)
                        in_keywords = False
                    else:
                        in_keywords = True
                elif in_keywords and stripped.startswith("-"):
                    keyword = stripped.lstrip("-").strip().strip('"').strip("'").lower()
                    if keyword:
                        keywords.append(keyword)
                else:
                    in_keywords = False

        if not keywords:
            keywords = _keywords_from_text(display_name)[:5]
        result.append(
            {
                "slug": slug,
                "display_name": display_name,
                "keywords": keywords[:10],
                "last_seen": last_seen,
                "status": "active",
            }
        )
    return result


def _load_hot_slugs() -> list[str]:
    hot_path = get_data_dir() / "hot.md"
    if not hot_path.exists():
        return []
    slugs: list[str] = []
    for line in hot_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        token = stripped[2:].split("|", 1)[0].strip()
        if token:
            slugs.append(token)
    return slugs


def _prune_topics(topics: list[dict[str, Any]], hot_slugs: list[str], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hot_set = set(hot_slugs)
    event_keywords = set()
    for event in events:
        event_keywords.update(_keywords_from_text(str(event.get("content_text") or "")))

    scored: list[tuple[int, dict[str, Any]]] = []
    for topic in topics:
        keywords = {str(item).lower() for item in (topic.get("keywords") or [])}
        overlap = len(keywords & event_keywords)
        score = overlap + (5 if topic.get("slug") in hot_set else 0)
        if score > 0:
            scored.append((score, topic))

    scored.sort(key=lambda item: item[0], reverse=True)
    pruned = [item[1] for item in scored[:30]]
    return pruned


def _component_time_range(component_events: list[dict[str, Any]]) -> tuple[str, str]:
    times: list[datetime] = []
    for event in component_events:
        ts = str(event.get("ts_start") or "")
        times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
    times.sort()
    start = times[0].astimezone(timezone.utc).strftime("%H:%M")
    end = times[-1].astimezone(timezone.utc).strftime("%H:%M")
    return start, end


def _topic_display_name(topic_slug: str, topics_index: list[dict[str, Any]]) -> str:
    for item in topics_index:
        if str(item.get("slug")) == topic_slug:
            display = str(item.get("display_name") or "").strip()
            if display:
                return display
    return topic_slug.replace("-", " ")


def _event_link(date_str: str, event_id: str) -> str:
    return f"[[../.keypulse/events/{date_str}/{event_id}|{event_id}]]"


def _upsert_topic(
    *,
    date_str: str,
    trigger: str,
    slug: str,
    display_name: str,
    keywords: list[str],
    narrative: str,
    event_ids: list[str],
) -> str:
    topics_dir = get_data_dir() / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    path = topics_dir / f"{slug}.md"
    now_date = date_str

    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    first_seen = now_date
    if existing_text:
        for line in existing_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("first_seen:"):
                first_seen = stripped.split(":", 1)[1].strip() or now_date
                break

    keyword_values = []
    seen: set[str] = set()
    for item in keywords:
        normalized = str(item).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        keyword_values.append(normalized)
    if len(keyword_values) < 5:
        keyword_values.extend([f"kw{i}" for i in range(len(keyword_values) + 1, 6)])
    keyword_values = keyword_values[:10]

    entry_line = f"- {date_str} {trigger} | {len(event_ids)} events | {narrative}"
    evidence_lines = [f"- {_event_link(date_str, event_id)}" for event_id in event_ids]

    if existing_text:
        lines = existing_text.splitlines()
        if entry_line not in existing_text:
            lines.append(entry_line)
        for evidence in evidence_lines:
            if evidence not in existing_text:
                lines.append(evidence)
        body = "\n".join(lines).rstrip() + "\n"
    else:
        body = "\n".join(
            [
                "---",
                "type: topic",
                f"slug: {slug}",
                f"display_name: {display_name}",
                f"first_seen: {first_seen}",
                f"last_seen: {now_date}",
                "keywords:",
                *[f"  - {item}" for item in keyword_values],
                "---",
                "",
                f"# {display_name}",
                "",
                "## Entries",
                entry_line,
                "",
                "## Related Events",
                *evidence_lines,
                "",
            ]
        )

    # typed diff: update mutable keys in place
    body = re.sub(r"(?m)^display_name:\s*.*$", f"display_name: {display_name}", body)
    body = re.sub(r"(?m)^last_seen:\s*.*$", f"last_seen: {now_date}", body)

    atomic_write_text(path, body)
    return "updated" if existing_text else "created"


def _refresh_hot(topics_touched: list[tuple[str, str]], date_str: str) -> None:
    hot_path = get_data_dir() / "hot.md"
    existing: dict[str, str] = {}
    if hot_path.exists():
        for line in hot_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            token = stripped[2:]
            slug, _, tail = token.partition("|")
            slug = slug.strip()
            if not slug:
                continue
            existing[slug] = tail.strip() or date_str
    for slug, _display in topics_touched:
        existing[slug] = date_str

    lines = ["# hot topics", ""]
    for slug, last_seen in sorted(existing.items(), key=lambda item: item[1], reverse=True)[:50]:
        lines.append(f"- {slug} | {last_seen}")
    lines.append("")
    atomic_write_text(hot_path, "\n".join(lines))


def _append_log(record: dict[str, Any]) -> None:
    log_path = get_data_dir() / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _daily_path(date_str: str) -> Path:
    sink = resolve_active_sink(Config.load(), persist=False)
    target = sink.output_dir / "Daily" / f"{date_str}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _render_daily_markdown(date_str: str, clusters: list[DailyCluster], misc_event_ids: list[str]) -> str:
    lines = [f"# {date_str}", "", "## 今日主线", ""]
    for cluster in clusters:
        if cluster.topic_action == "misc":
            continue
        lines.append(f"### {cluster.display_name}")
        lines.append(cluster.narrative)
        links = " ".join(_event_link(date_str, event_id) for event_id in cluster.event_ids)
        lines.append(f"- 关联事件: {links}")
        lines.append("")

    lines.extend(["## 散点", ""])
    if misc_event_ids:
        for event_id in misc_event_ids:
            lines.append(f"- {_event_link(date_str, event_id)}")
    else:
        lines.append("- （无）")
    lines.append("")
    return "\n".join(lines)


def _cost_snapshot(run_started_at: datetime) -> dict[str, Any]:
    path = get_data_dir() / "cost.jsonl"
    if not path.exists():
        return {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0}

    in_tokens = 0
    out_tokens = 0
    cost_usd = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts_text = str(payload.get("ts") or "")
        try:
            ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < run_started_at:
            continue
        in_tokens += int(payload.get("in_tokens") or 0)
        out_tokens += int(payload.get("out_tokens") or 0)
        cost_usd += float(payload.get("cost_usd") or 0.0)
    return {"in_tokens": in_tokens, "out_tokens": out_tokens, "cost_usd": round(cost_usd, 8)}


def _load_rows_for_date(date_str: str) -> list[dict[str, Any]]:
    since, until = local_day_bounds(date_str)
    rows = query_raw_events(since=since, until=until, limit=50000)
    return sorted(rows, key=lambda item: (str(item.get("ts_start") or ""), int(item.get("id") or 0)))


def _filter_for_trigger(date_str: str, trigger: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state = _read_state()
    dates = state.setdefault("dates", {})
    day_state = dates.setdefault(date_str, {})

    if trigger == "18:00":
        if rows:
            day_state["last_1800_event_id"] = int(rows[-1].get("id") or 0)
        _write_state(state)
        return rows

    cutoff = int(day_state.get("last_1800_event_id") or 0)
    pending = [row for row in rows if int(row.get("id") or 0) > cutoff]
    return pending


def run_daily(date_str: str, *, trigger: str = "18:00") -> DailySummary:
    if trigger not in _TRIGGER_VALUES:
        raise ValueError(f"invalid trigger: {trigger}")

    run_started_at = datetime.now(timezone.utc)
    rows = _load_rows_for_date(date_str)
    scoped_rows = _filter_for_trigger(date_str, trigger, rows)
    events = [_extract_event_payload(row) for row in scoped_rows]

    if len(events) < 3:
        daily_path = _daily_path(date_str)
        body = "\n".join([f"# {date_str}", "", "## 今日主线", "", "- 今日事件不足 3 条，跳过聚类。", ""])
        atomic_write_text(daily_path, body)
        summary_path = write_daily_summary(
            date_str,
            clusters=[],
            misc=[str(event.get("id")) for event in events],
            topic_snapshot={},
            cost=_cost_snapshot(run_started_at),
        )
        _append_log(
            {
                "ts": _now_iso(),
                "capability": "daily_orchestrator",
                "date": date_str,
                "trigger": trigger,
                "decision": "skip_low_volume",
                "event_count": len(events),
            }
        )
        return DailySummary(
            date=date_str,
            trigger=trigger,
            event_count=len(rows),
            processed_count=len(events),
            cluster_count=0,
            misc_event_ids=tuple(str(event.get("id")) for event in events),
            topic_diffs=tuple(),
            daily_path=str(daily_path),
            summary_path=str(summary_path),
            skipped=True,
        )

    graph = build_evidence_graph(events)
    components = connected_components(graph)
    feature_index = build_feature_index(events)
    merge_candidates = detect_merge_candidates(components, 0.2, feature_index=feature_index)
    component_map = {f"c{index+1}": sorted(list(component)) for index, component in enumerate(components)}
    topics = _load_topic_index()
    hot_slugs = _load_hot_slugs()
    pruned_topics = _prune_topics(topics, hot_slugs, events)

    gateway = _load_gateway()

    component_payloads: list[dict[str, Any]] = []
    for component_id, event_ids in component_map.items():
        component_events = [event for event in events if str(event.get("id")) in set(event_ids)]
        feature = component_features(set(event_ids), feature_index)
        time_range = list(_component_time_range(component_events))
        component_payloads.append(
            {
                "component_id": component_id,
                "event_ids": event_ids,
                "time_range": time_range,
                "h1_entities": sorted(feature["entities"]),
                "h2_contexts": [],
                "keywords": sorted(feature["keywords"])[:20],
            }
        )

    l1_input = {
        "scope_date": date_str,
        "trigger": trigger,
        "components": component_payloads,
        "merge_candidates": [list(pair) for pair in merge_candidates],
        "existing_topics_index": pruned_topics,
        "hot_cache": hot_slugs,
        "hud_input_today": None,
    }

    try:
        from keypulse.prompts.loader import load_prompt

        l1_spec = load_prompt("L1_cluster_review")
        l1_prompt = _build_prompt(l1_spec.body, "L1_cluster_review", l1_input)
        l1_output = gateway.call(
            "L1_cluster_review",
            l1_prompt,
            input_data=l1_input,
        )
    except (LLMCallError, ValueError, KeyError, OSError) as exc:
        raise DailyOrchestratorError(f"L1 failed: {exc}") from exc

    if not isinstance(l1_output, dict):
        raise DailyOrchestratorError("L1 output must be object")

    decision_map: dict[str, dict[str, Any]] = {}
    for item in l1_output.get("clusters", []):
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or "").strip()
        if component_id:
            decision_map[component_id] = item

    misc_event_ids = [str(item) for item in (l1_output.get("misc_event_ids") or []) if str(item).strip()]
    valid_ids = {str(event.get("id")) for event in events}
    misc_event_ids = [event_id for event_id in misc_event_ids if event_id in valid_ids]

    resolved_clusters: list[DailyCluster] = []
    new_cluster_inputs: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    l2_inputs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    for component_id, event_ids in component_map.items():
        decision = decision_map.get(component_id, {"component_id": component_id, "topic_action": "misc"})
        topic_action = str(decision.get("topic_action") or "misc")
        if topic_action not in {"existing", "new", "misc"}:
            topic_action = "misc"
        component_events = [event for event in events if str(event.get("id")) in set(event_ids)]

        topic_slug = str(decision.get("topic_slug") or "").strip() or None
        if topic_action == "existing" and topic_slug is None:
            topic_action = "misc"
        if topic_action == "misc":
            for event_id in event_ids:
                if event_id not in misc_event_ids:
                    misc_event_ids.append(event_id)

        if topic_action == "new":
            new_cluster_inputs.append((component_id, component_events, decision))

        l2_payload = {
            "scope_date": date_str,
            "trigger": trigger,
            "component_id": component_id,
            "event_ids": event_ids,
            "events": [
                {
                    "id": str(event.get("id")),
                    "timestamp": str(event.get("ts_start")),
                    "app": str(event.get("app_name") or "unknown"),
                    "content": str(event.get("content_text") or ""),
                }
                for event in component_events
            ],
            "topic_context": {
                "topic_action": topic_action,
                "topic_slug": topic_slug,
                "display_name": _topic_display_name(topic_slug, pruned_topics) if topic_slug else None,
            },
        }
        l2_inputs.append((component_id, l2_payload, decision))

    # L2 narratives in parallel
    l2_results: dict[str, str] = {}
    try:
        from keypulse.prompts.loader import load_prompt

        l2_spec = load_prompt("L2_narrative")
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(l2_inputs)))) as executor:
            futures = {}
            for component_id, payload, _decision in l2_inputs:
                prompt = _build_prompt(l2_spec.body, "L2_narrative", payload)
                futures[
                    executor.submit(
                        gateway.call,
                        "L2_narrative",
                        prompt,
                        input_data=payload,
                    )
                ] = component_id
            for future in as_completed(futures):
                component_id = futures[future]
                output = future.result()
                if isinstance(output, dict):
                    markdown = str(output.get("markdown") or "").strip()
                else:
                    markdown = str(output).strip()
                l2_results[component_id] = markdown
    except (LLMCallError, ValueError, KeyError, OSError, RuntimeError) as exc:
        raise DailyOrchestratorError(f"L2 failed: {exc}") from exc

    # L3 naming in parallel for new topics
    l3_results: dict[str, dict[str, Any]] = {}
    existing_slugs = {str(item.get("slug")) for item in pruned_topics if str(item.get("slug") or "").strip()}
    try:
        from keypulse.prompts.loader import load_prompt

        l3_spec = load_prompt("L3_topic_naming")
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(new_cluster_inputs)))) as executor:
            futures = {}
            for component_id, component_events, _decision in new_cluster_inputs:
                payload = {
                    "trigger": trigger,
                    "component_id": component_id,
                    "events": [
                        {
                            "id": str(event.get("id")),
                            "content": str(event.get("content_text") or ""),
                            "app": str(event.get("app_name") or ""),
                            "timestamp": str(event.get("ts_start") or ""),
                        }
                        for event in component_events
                    ],
                    "existing_slugs": sorted(existing_slugs),
                }
                prompt = _build_prompt(l3_spec.body, "L3_topic_naming", payload)
                futures[
                    executor.submit(
                        gateway.call,
                        "L3_topic_naming",
                        prompt,
                        input_data=payload,
                    )
                ] = component_id
            for future in as_completed(futures):
                component_id = futures[future]
                result = future.result()
                if isinstance(result, dict):
                    l3_results[component_id] = result
                    slug = str(result.get("slug") or "").strip()
                    if slug:
                        existing_slugs.add(slug)
    except (LLMCallError, ValueError, KeyError, OSError, RuntimeError) as exc:
        raise DailyOrchestratorError(f"L3 failed: {exc}") from exc

    topics_touched: list[tuple[str, str]] = []
    topic_diffs: list[str] = []
    for component_id, event_ids in component_map.items():
        decision = decision_map.get(component_id, {"topic_action": "misc"})
        topic_action = str(decision.get("topic_action") or "misc")
        component_events = [event for event in events if str(event.get("id")) in set(event_ids)]
        narrative = l2_results.get(component_id, "").strip()
        if not narrative:
            start, end = _component_time_range(component_events)
            narrative = f"本时段共 {len(event_ids)} 条相关事件，时间范围 {start}-{end}。"

        topic_slug: str | None = None
        display_name = "散点"
        if topic_action == "existing":
            topic_slug = str(decision.get("topic_slug") or "").strip() or None
            if topic_slug:
                display_name = _topic_display_name(topic_slug, pruned_topics)
        elif topic_action == "new":
            named = l3_results.get(component_id, {})
            candidate_slug = str(named.get("slug") or "").strip()
            if candidate_slug and _SLUG_RE.fullmatch(candidate_slug):
                topic_slug = candidate_slug
            else:
                topic_slug = _slugify_topic(narrative, fallback=f"topic-{date_str.replace('-', '')}-{component_id.lower()}")
            display_name = str(named.get("display_name") or topic_slug).strip() or topic_slug
        else:
            topic_action = "misc"

        if topic_action != "misc" and topic_slug:
            keywords = []
            if topic_action == "new":
                keywords = [str(item) for item in (l3_results.get(component_id, {}).get("keywords") or [])]
            else:
                for item in pruned_topics:
                    if str(item.get("slug") or "") == topic_slug:
                        keywords = [str(v) for v in (item.get("keywords") or [])]
                        break
            diff = _upsert_topic(
                date_str=date_str,
                trigger=trigger,
                slug=topic_slug,
                display_name=display_name,
                keywords=keywords,
                narrative=narrative,
                event_ids=event_ids,
            )
            topic_diffs.append(f"{topic_slug}:{diff}")
            topics_touched.append((topic_slug, display_name))

        resolved_clusters.append(
            DailyCluster(
                component_id=component_id,
                topic_action=topic_action,
                topic_slug=topic_slug,
                display_name=display_name,
                event_ids=tuple(event_ids),
                narrative=narrative,
                merge_with_component=str(decision.get("merge_with_component") or "").strip() or None,
            )
        )

    daily_path = _daily_path(date_str)
    daily_markdown = _render_daily_markdown(date_str, resolved_clusters, misc_event_ids)
    atomic_write_text(daily_path, daily_markdown)

    _refresh_hot(topics_touched, date_str)
    _append_log(
        {
            "ts": _now_iso(),
            "capability": "daily_orchestrator",
            "date": date_str,
            "trigger": trigger,
            "cluster_count": len(resolved_clusters),
            "misc_count": len(misc_event_ids),
            "topic_diffs": topic_diffs,
            "merge_candidates": merge_candidates,
        }
    )

    summary_clusters = []
    for cluster in resolved_clusters:
        if cluster.topic_action == "misc":
            continue
        cluster_events = [event for event in events if str(event.get("id")) in set(cluster.event_ids)]
        start, end = _component_time_range(cluster_events)
        summary_clusters.append(
            {
                "slug": cluster.topic_slug or "misc",
                "display_name": cluster.display_name,
                "narrative_one_line": cluster.narrative[:120],
                "event_count": len(cluster.event_ids),
                "time_range": [start, end],
                "merge_candidate_with": [cluster.merge_with_component] if cluster.merge_with_component else [],
            }
        )
    summary_path = write_daily_summary(
        date_str,
        clusters=summary_clusters,
        misc=misc_event_ids,
        topic_snapshot={slug: "active" for slug, _ in topics_touched},
        cost=_cost_snapshot(run_started_at),
    )

    return DailySummary(
        date=date_str,
        trigger=trigger,
        event_count=len(rows),
        processed_count=len(events),
        cluster_count=len(resolved_clusters),
        misc_event_ids=tuple(misc_event_ids),
        topic_diffs=tuple(topic_diffs),
        daily_path=str(daily_path),
        summary_path=str(summary_path),
        skipped=False,
    )
