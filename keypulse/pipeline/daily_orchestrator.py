from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
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
from keypulse.pipeline.daily_strategy import (
    BudgetStrategyDeps,
    BudgetTwoStepStrategy,
    ClusterRecord,
    DailyStrategyError,
    FlagshipSingleStepStrategy,
)
from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.model import LLMCallError, ModelGateway, load_model_gateway
from keypulse.pipeline.model_card import resolve_tier
from keypulse.store.repository import query_raw_events
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.dates import local_day_bounds
from keypulse.utils.paths import get_data_dir


_TRIGGER_VALUES = {"18:00", "23:30"}
_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,29}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKENISH_RE = re.compile(r"[a-zA-Z0-9_./:-]+")
_TOOL_ECHO_SOURCES = frozenset({"ax_text", "ocr_text", "window", "idle", "knowledgec", "zsh_history"})
_USER_MESSAGE_SOURCES = frozenset({"clipboard", "manual", "markdown_vault", "claude_code", "codex_cli"})


class DailyOrchestratorError(RuntimeError):
    """Raised when daily orchestration cannot complete and caller should fallback."""


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
        "source": str(row["source"] or "").strip(),
        "speaker": str(row["speaker"] or "").strip(),
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


def _estimate_token_count(text: str) -> int:
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    tokenish_count = len(_TOKENISH_RE.findall(text))
    return cjk_count + tokenish_count


def _source_kind(event: Mapping[str, Any]) -> str:
    speaker = str(event.get("speaker") or "").strip().lower()
    source = str(event.get("source") or "").strip().lower()
    if speaker in {"ai", "assistant"}:
        return "assistant_msg"
    if source in _TOOL_ECHO_SOURCES or speaker == "system":
        return "tool_echo"
    if speaker == "user" or source in _USER_MESSAGE_SOURCES:
        return "user_msg"
    return "default"


def _event_value_density(event: Mapping[str, Any], settings: Any | None = None) -> float:
    cfg = settings or Config().pipeline.value_density
    if not bool(getattr(cfg, "enabled", True)):
        return 0.0

    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"))
        if str(part or "").strip()
    )
    token_target = max(int(getattr(cfg, "token_target", 80) or 80), 1)
    base = min(_estimate_token_count(text) / token_target, 1.0)
    weights = getattr(cfg, "source_weights", {}) or {}
    weight = float(weights.get(_source_kind(event), weights.get("default", 0.6)) or 0.0)
    score = base * max(weight, 0.0)

    decision_regex = str(getattr(cfg, "decision_regex", "") or "").strip()
    if decision_regex:
        try:
            if re.search(decision_regex, text):
                score += max(float(getattr(cfg, "decision_bonus", 0.0) or 0.0), 0.0)
        except re.error:
            pass

    return round(min(max(score, 0.0), 1.0), 4)


def _component_size_score(event_count: int) -> float:
    return round(min(max(event_count, 0) / 5.0, 0.7), 4)


def _component_density_metadata(component_events: list[dict[str, Any]], settings: Any | None = None) -> dict[str, float]:
    densities = [_event_value_density(event, settings) for event in component_events]
    peak = max(densities, default=0.0)
    size_score = _component_size_score(len(component_events))
    return {
        "size_score": size_score,
        "peak_event_density": peak,
        "importance_score": round(max(size_score, peak), 4),
    }


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
    date_str = str(payload.get("date") or "unknown-date")
    clusters = [item for item in (payload.get("clusters") or []) if isinstance(item, dict)]
    lines = [
        "📍 Asia/Shanghai",
        "",
        f"# {date_str}",
        "",
        "## 今日要点",
        "",
        "你今天把分散事件收拢成可复盘的主题叙事，重点不是操作数量，而是确认了哪些工作线索值得沉淀，以及哪些噪音可以被排除在日报主体之外。",
        "",
        "## 今天做的事",
        "",
    ]
    if not clusters:
        lines.extend(["### 日常推进", "", "你围绕同一条工作线持续推进，留下了足够的上下文用于回看。"])
    for cluster in clusters:
        display_name = str(cluster.get("display_name") or "日常推进")
        events = [item for item in (cluster.get("events") or []) if isinstance(item, dict)]
        content = "；".join(str(item.get("c") or "").strip() for item in events[:2]) or "处理关键任务"
        lines.extend(
            [
                f"### {display_name}",
                "",
                f"你围绕「{display_name}」推进了连续事项，输入里能看到 {content}。这组事件形成了相对完整的上下文，适合沉淀为主题而不是散点记录。",
                "",
            ]
        )
    lines.extend(["## 明日的锚点", "", "> 明天我想：______", ">", "> _写一句话留给明天的自己_", ""])
    return {"markdown": "\n".join(lines)}


def _mock_daily_flagship_output(payload: dict[str, Any]) -> dict[str, str]:
    return _mock_l2_output({"date": payload.get("date"), "clusters": [{"display_name": "全天主线", "events": payload.get("events") or []}], "misc_events": []})


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
        elif capability == "daily_flagship":
            body = json.dumps(_mock_daily_flagship_output(payload), ensure_ascii=False)
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


def _backend_name_for_tier(backend_model: str, cfg: Config) -> str:
    if backend_model == cfg.model.cloud.model:
        return "cloud"
    if backend_model == cfg.model.local.model:
        return "local"
    return "cloud"


def _resolve_daily_tier(gateway: ModelGateway) -> str:
    backend = gateway.select_backend(stage="write")
    cfg = Config.load()
    backend_name = _backend_name_for_tier(backend.model, cfg)
    backend_cfg = getattr(cfg.model, backend_name)
    tier_override = getattr(backend_cfg, "tier", "")
    return resolve_tier(backend.model, override=tier_override)


def _extract_narrative_one_line(markdown: str, display_name: str) -> str:
    lines = markdown.splitlines()
    target_heading = f"### {display_name}".strip()
    in_section = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            if in_section:
                break
            in_section = stripped == target_heading
            continue
        if in_section and stripped and not stripped.startswith("#"):
            collected.append(stripped.lstrip("> ").strip())
    return " ".join(collected)[:120]


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

    gateway = _load_gateway()
    tier = _resolve_daily_tier(gateway)

    if tier == "flagship":
        strategy = FlagshipSingleStepStrategy()
        try:
            result = strategy.generate(date_str=date_str, events=events, gateway=gateway)
        except DailyStrategyError as exc:
            raise DailyOrchestratorError(str(exc)) from exc

        daily_path = _daily_path(date_str)
        atomic_write_text(daily_path, result.markdown)
        _append_log(
            {
                "ts": _now_iso(),
                "capability": "daily_orchestrator",
                "date": date_str,
                "trigger": trigger,
                "tier": "flagship",
                "strategy": strategy.name,
            }
        )
        summary_path = write_daily_summary(
            date_str,
            clusters=[],
            misc=[],
            topic_snapshot={},
            cost=_cost_snapshot(run_started_at),
        )
        return DailySummary(
            date=date_str,
            trigger=trigger,
            event_count=len(rows),
            processed_count=len(events),
            cluster_count=0,
            misc_event_ids=tuple(),
            topic_diffs=tuple(),
            daily_path=str(daily_path),
            summary_path=str(summary_path),
            skipped=False,
        )

    density_settings = Config.load().pipeline.value_density
    merge_cache: dict[str, Any] = {"pairs": []}

    def cluster_components(scoped_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        graph = build_evidence_graph(scoped_events)
        components = connected_components(graph)
        feature_index = build_feature_index(scoped_events)
        raw_merge_candidates = detect_merge_candidates(components, 0.2, feature_index=feature_index)
        component_ids = [f"c{index+1}" for index in range(len(components))]
        signature_to_component_id = {
            ",".join(sorted(component)): component_ids[index] for index, component in enumerate(components)
        }
        merge_cache["pairs"] = [
            (signature_to_component_id[left], signature_to_component_id[right])
            for left, right in raw_merge_candidates
            if left in signature_to_component_id and right in signature_to_component_id
        ]

        payloads: list[dict[str, Any]] = []
        for component_id, component in zip(component_ids, components, strict=False):
            event_ids = sorted(list(component))
            event_id_set = set(event_ids)
            component_events = [event for event in scoped_events if str(event.get("id")) in event_id_set]
            feature = component_features(event_id_set, feature_index)
            density_metadata = _component_density_metadata(component_events, density_settings)
            high_density_threshold = float(getattr(density_settings, "high_density_threshold", 0.75) or 0.75)
            payloads.append(
                {
                    "component_id": component_id,
                    "event_ids": event_ids,
                    "time_range": list(_component_time_range(component_events)),
                    "h1_entities": sorted(feature["entities"]),
                    "h2_contexts": [],
                    "keywords": sorted(feature["keywords"])[:20],
                    "high_density_threshold": high_density_threshold,
                    **density_metadata,
                }
            )
        return payloads

    deps = BudgetStrategyDeps(
        cluster_components=cluster_components,
        load_topics_index=_load_topic_index,
        load_hot_slugs=_load_hot_slugs,
        prune_topics=_prune_topics,
        topic_display_name=_topic_display_name,
        detect_merges=lambda _component_payloads: list(merge_cache.get("pairs") or []),
    )
    strategy = BudgetTwoStepStrategy(deps)
    try:
        result = strategy.generate(date_str=date_str, events=events, gateway=gateway)
    except DailyStrategyError as exc:
        raise DailyOrchestratorError(str(exc)) from exc

    cluster_records = list(result.clusters)
    events_by_id = {str(event.get("id")): event for event in events}
    new_cluster_inputs: list[tuple[ClusterRecord, list[dict[str, Any]]]] = []
    for cluster in cluster_records:
        if cluster.topic_action != "new":
            continue
        component_events = [events_by_id[event_id] for event_id in cluster.event_ids if event_id in events_by_id]
        new_cluster_inputs.append((cluster, component_events))

    l3_results: dict[str, dict[str, Any]] = {}
    topics_index = _load_topic_index()
    existing_slugs = {str(item.get("slug")) for item in topics_index if str(item.get("slug") or "").strip()}
    if new_cluster_inputs:
        try:
            from keypulse.prompts.loader import load_prompt

            l3_spec = load_prompt("L3_topic_naming")
            with ThreadPoolExecutor(max_workers=min(8, len(new_cluster_inputs))) as executor:
                futures = {}
                for cluster, component_events in new_cluster_inputs:
                    payload = {
                        "trigger": trigger,
                        "component_id": cluster.component_id,
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
                    ] = cluster.component_id
                for future in as_completed(futures):
                    component_id = futures[future]
                    try:
                        named = future.result()
                    except (LLMCallError, ValueError, KeyError, OSError, RuntimeError):
                        continue
                    if isinstance(named, dict):
                        l3_results[component_id] = named
                        slug = str(named.get("slug") or "").strip()
                        if slug:
                            existing_slugs.add(slug)
        except (LLMCallError, ValueError, KeyError, OSError, RuntimeError):
            l3_results = {}

    topics_touched: list[tuple[str, str]] = []
    topic_diffs: list[str] = []
    resolved_clusters: list[ClusterRecord] = []
    for cluster in cluster_records:
        resolved = cluster
        if cluster.topic_action == "new":
            named = l3_results.get(cluster.component_id, {})
            candidate_slug = str(named.get("slug") or "").strip()
            if candidate_slug and _SLUG_RE.fullmatch(candidate_slug):
                topic_slug = candidate_slug
            else:
                fallback_text = " ".join(
                    str(events_by_id[event_id].get("content_text") or "")
                    for event_id in cluster.event_ids
                    if event_id in events_by_id
                )
                topic_slug = _slugify_topic(
                    fallback_text or cluster.display_name,
                    fallback=f"topic-{date_str.replace('-', '')}-{cluster.component_id.lower()}",
                )
            display_name = str(named.get("display_name") or topic_slug).strip() or topic_slug
            resolved = replace(cluster, topic_slug=topic_slug, display_name=display_name)

        narrative = _extract_narrative_one_line(result.markdown, resolved.display_name)
        if not narrative:
            component_events = [events_by_id[event_id] for event_id in resolved.event_ids if event_id in events_by_id]
            start, end = _component_time_range(component_events)
            narrative = f"本主题共 {len(resolved.event_ids)} 条相关事件，时间范围 {start}-{end}。"
        resolved = replace(resolved, narrative_one_line=narrative)

        keywords = list(resolved.keywords)
        if resolved.topic_action == "new":
            keywords = [str(item) for item in (l3_results.get(resolved.component_id, {}).get("keywords") or [])] or keywords
        else:
            for item in topics_index:
                if str(item.get("slug") or "") == resolved.topic_slug:
                    keywords = [str(v) for v in (item.get("keywords") or [])]
                    break

        diff = _upsert_topic(
            date_str=date_str,
            trigger=trigger,
            slug=resolved.topic_slug,
            display_name=resolved.display_name,
            keywords=keywords,
            narrative=narrative,
            event_ids=list(resolved.event_ids),
        )
        topic_diffs.append(f"{resolved.topic_slug}:{diff}")
        topics_touched.append((resolved.topic_slug, resolved.display_name))
        resolved_clusters.append(resolved)

    daily_path = _daily_path(date_str)
    atomic_write_text(daily_path, result.markdown)

    _refresh_hot(topics_touched, date_str)
    _append_log(
        {
            "ts": _now_iso(),
            "capability": "daily_orchestrator",
            "date": date_str,
            "trigger": trigger,
            "tier": "budget",
            "strategy": strategy.name,
            "cluster_count": len(resolved_clusters),
            "misc_count": len(result.misc_event_ids),
            "topic_diffs": topic_diffs,
            "merge_candidates": list(result.merge_candidates),
        }
    )

    merge_map: dict[str, list[str]] = {}
    for left, right in result.merge_candidates:
        merge_map.setdefault(left, []).append(right)
        merge_map.setdefault(right, []).append(left)

    summary_clusters = []
    for cluster in resolved_clusters:
        component_events = [events_by_id[event_id] for event_id in cluster.event_ids if event_id in events_by_id]
        start, end = _component_time_range(component_events)
        summary_clusters.append(
            {
                "slug": cluster.topic_slug,
                "display_name": cluster.display_name,
                "narrative_one_line": cluster.narrative_one_line,
                "event_count": len(cluster.event_ids),
                "time_range": [start, end],
                "merge_candidate_with": merge_map.get(cluster.component_id, []),
                "peak_event_density": cluster.peak_event_density,
            }
        )
    summary_path = write_daily_summary(
        date_str,
        clusters=summary_clusters,
        misc=list(result.misc_event_ids),
        topic_snapshot={slug: "active" for slug, _ in topics_touched},
        cost=_cost_snapshot(run_started_at),
    )

    return DailySummary(
        date=date_str,
        trigger=trigger,
        event_count=len(rows),
        processed_count=len(events),
        cluster_count=len(resolved_clusters),
        misc_event_ids=tuple(result.misc_event_ids),
        topic_diffs=tuple(topic_diffs),
        daily_path=str(daily_path),
        summary_path=str(summary_path),
        skipped=False,
    )
