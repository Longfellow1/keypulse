from __future__ import annotations

import json
import re
import hashlib
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any

from keypulse.utils.paths import get_data_dir


_TIME_TEXT = re.compile(r"^\d{2}:\d{2}$")
_CLUSTER_KEYS = {
    "slug",
    "display_name",
    "narrative_one_line",
    "event_count",
    "time_range",
    "merge_candidate_with",
}
_OPTIONAL_CLUSTER_KEYS = {"peak_event_density"}
_EVENT_KEYS = {
    "cluster_id",
    "display_name",
    "narrative_one_line",
    "event_count",
    "time_range",
    "anchored_to",
}
_OPTIONAL_EVENT_KEYS = {"peak_event_density", "merge_candidate_with"}
_TOPIC_KEYS = {"anchor", "anchor_state", "narrative", "decisions", "shipped", "events_ref"}
_COST_KEYS = {"in_tokens", "out_tokens", "cost_usd"}
_TOPIC_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_TIME_IN_TEXT_RE = re.compile(r"\b([01]\d|2[0-3]):([0-5]\d)\b")
_EVENT_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _slugify_narrative_topic(name: str) -> str:
    words = _ASCII_WORD_RE.findall(name.lower())
    slug = "-".join(words)[:40].strip("-")
    if len(slug) >= 3 and slug[0].isalpha():
        return slug
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"topic-{digest}"


def _infer_topic_state(text: str) -> str:
    normalized = str(text or "")
    completed_markers = ("完成", "解决", "修复", "通过", "确认", "写完", "提交", "落完", "重启")
    blocked_markers = ("卡点", "阻塞", "失败", "报错", "错误", "疑惑", "400", "无法")
    progress_markers = ("推进", "重构", "优化", "排查", "处理", "讨论", "调整", "配置", "同步")
    started_markers = ("开始", "启动", "提出", "规划", "浏览", "查看", "查询", "登录", "第一次", "首次")

    if any(marker in normalized for marker in completed_markers):
        return "completed"
    if any(marker in normalized for marker in blocked_markers):
        return "blocked"
    if any(marker in normalized for marker in progress_markers):
        return "in_progress"
    if any(marker in normalized for marker in started_markers):
        return "started"
    return "in_progress"


def _extract_things_sections(markdown: str) -> list[tuple[str, str]]:
    lines = str(markdown or "").splitlines()
    in_things = False
    current_name = ""
    current_body: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal current_name, current_body
        if current_name.strip():
            sections.append((current_name.strip(), "\n".join(current_body).strip()))
        current_name = ""
        current_body = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("# "):
            heading_text = stripped.lstrip("#").strip()
            if "明日" in heading_text or "明天" in heading_text or "事件卡" in heading_text or "涉及的主题" in heading_text:
                if in_things:
                    flush()
                in_things = False
                continue
            if "今天做的事" in heading_text or "今日做的事" in heading_text:
                if in_things:
                    flush()
                in_things = True
                continue
            if in_things and stripped.startswith("## ") and "概览" not in heading_text:
                flush()
                in_things = False
                continue

        if not in_things:
            continue

        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            current_body = []
            continue

        if current_name:
            current_body.append(line)

    if in_things:
        flush()
    return sections


def build_cluster_stubs_from_narrative(date: str, markdown: str) -> list[dict[str, Any]]:
    date_text = _validate_date(date)
    sections = _extract_things_sections(markdown)
    if not sections:
        return []

    stubs: list[dict[str, Any]] = []
    for name, body in sections:
        if not name or name in {"其他"}:
            continue
        slug = _slugify_narrative_topic(name)
        clean_body = " ".join(line.strip() for line in body.splitlines() if line.strip())
        one_line = clean_body[:120] if clean_body else f"{name} 在 {date_text} 有连续推进。"
        times = [match.group(0) for match in _TIME_IN_TEXT_RE.finditer(body)]
        if times:
            time_range = [min(times), max(times)]
        else:
            time_range = ["00:00", "23:59"]
        stubs.append(
            {
                "slug": slug,
                "display_name": name,
                "narrative_one_line": one_line,
                "event_count": max(1, len(re.findall(r"[。；;.!?！？]", clean_body)) or 1),
                "time_range": time_range,
                "merge_candidate_with": [],
            }
        )
    return stubs


def build_topic_status_snapshot_from_narrative(date: str, markdown: str) -> dict[str, dict[str, Any]]:
    """Infer daily topic status from the existing Things narrative.

    The daily flagship path may only persist the rendered Markdown, leaving
    `clusters` empty. This parser treats each H3 under a "今天做的事"/"今日做的事"
    narrative section as one topic, derives a stable ASCII slug from the heading
    (falling back to a short content hash for Chinese-only names), and classifies
    state with conservative keyword rules. It does not run clustering, entity
    extraction, or any LLM call; evidence is the current daily note date.
    """

    date_text = _validate_date(date)
    sections = _extract_things_sections(markdown)

    snapshot: dict[str, dict[str, Any]] = {}
    for name, body in sections:
        if not name or name in {"其他"}:
            continue
        slug = _slugify_narrative_topic(name)
        state = _infer_topic_state(f"{name}\n{body}")
        existing = snapshot.get(slug)
        if existing is None:
            snapshot[slug] = {
                "name": name,
                "state": state,
                "last_seen_date": date_text,
                "evidence_dates": [date_text],
            }
            continue
        dates = list(existing.get("evidence_dates") or [])
        if date_text not in dates:
            dates.append(date_text)
        snapshot[slug] = {
            "name": str(existing.get("name") or name),
            "state": _merge_topic_states(str(existing.get("state") or ""), state),
            "last_seen_date": date_text,
            "evidence_dates": sorted(dates),
        }
    return snapshot


def _merge_topic_states(left: str, right: str) -> str:
    priority = {"started": 0, "in_progress": 1, "blocked": 2, "completed": 3}
    left_value = left if left in priority else "started"
    right_value = right if right in priority else "started"
    return left_value if priority[left_value] >= priority[right_value] else right_value


def merge_topic_status_snapshots(
    snapshots: list[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for slug, payload in snapshot.items():
            if not isinstance(payload, dict):
                continue
            key = str(slug or "").strip()
            if not key:
                continue
            dates = [str(item) for item in (payload.get("evidence_dates") or []) if str(item).strip()]
            last_seen = str(payload.get("last_seen_date") or (dates[-1] if dates else "")).strip()
            current = result.get(key)
            if current is None:
                result[key] = {
                    "name": str(payload.get("name") or key).strip() or key,
                    "state": str(payload.get("state") or "started").strip() or "started",
                    "last_seen_date": last_seen,
                    "evidence_dates": sorted(set(dates)),
                }
                continue
            merged_dates = sorted(set([*list(current.get("evidence_dates") or []), *dates]))
            result[key] = {
                "name": str(current.get("name") or payload.get("name") or key),
                "state": _merge_topic_states(str(current.get("state") or ""), str(payload.get("state") or "")),
                "last_seen_date": max(str(current.get("last_seen_date") or ""), last_seen),
                "evidence_dates": merged_dates,
            }
    return result


def _summary_dir() -> Path:
    target = get_data_dir() / "daily-summary"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _validate_date(date_text: str) -> str:
    candidate = str(date_text).strip()
    try:
        date_cls.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid date: {date_text}") from exc
    return candidate


def _validate_cluster(cluster: Any, index: int) -> dict[str, Any]:
    if not isinstance(cluster, dict):
        raise ValueError(f"clusters[{index}] must be object")

    keys = set(cluster.keys())
    missing = sorted(_CLUSTER_KEYS - keys)
    extra = sorted(keys - _CLUSTER_KEYS - _OPTIONAL_CLUSTER_KEYS)
    if missing:
        raise ValueError(f"clusters[{index}] missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"clusters[{index}] unexpected fields: {', '.join(extra)}")

    slug = str(cluster["slug"])
    display_name = str(cluster["display_name"])
    narrative_one_line = str(cluster["narrative_one_line"])

    event_count = cluster["event_count"]
    if not isinstance(event_count, int) or isinstance(event_count, bool):
        raise ValueError(f"clusters[{index}].event_count must be integer")

    time_range = cluster["time_range"]
    if not isinstance(time_range, list) or len(time_range) != 2:
        raise ValueError(f"clusters[{index}].time_range must be [start, end]")
    start = str(time_range[0])
    end = str(time_range[1])
    if _TIME_TEXT.fullmatch(start) is None or _TIME_TEXT.fullmatch(end) is None:
        raise ValueError(f"clusters[{index}].time_range items must be HH:MM")

    merge_with = cluster["merge_candidate_with"]
    if not isinstance(merge_with, list):
        raise ValueError(f"clusters[{index}].merge_candidate_with must be array")
    merge_candidate_with = [str(item) for item in merge_with]

    result = {
        "slug": slug,
        "display_name": display_name,
        "narrative_one_line": narrative_one_line,
        "event_count": event_count,
        "time_range": [start, end],
        "merge_candidate_with": merge_candidate_with,
    }
    if "peak_event_density" in cluster:
        result["peak_event_density"] = float(cluster["peak_event_density"] or 0.0)
    return result


def _validate_event(event: Any, index: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError(f"events[{index}] must be object")

    keys = set(event.keys())
    missing = sorted(_EVENT_KEYS - keys)
    extra = sorted(keys - _EVENT_KEYS - _OPTIONAL_EVENT_KEYS)
    if missing:
        raise ValueError(f"events[{index}] missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"events[{index}] unexpected fields: {', '.join(extra)}")

    cluster_id = str(event["cluster_id"])
    display_name = str(event["display_name"])
    narrative_one_line = str(event["narrative_one_line"])

    event_count = event["event_count"]
    if not isinstance(event_count, int) or isinstance(event_count, bool):
        raise ValueError(f"events[{index}].event_count must be integer")

    time_range = event["time_range"]
    if not isinstance(time_range, list) or len(time_range) != 2:
        raise ValueError(f"events[{index}].time_range must be [start, end]")
    start = str(time_range[0])
    end = str(time_range[1])
    if _TIME_TEXT.fullmatch(start) is None or _TIME_TEXT.fullmatch(end) is None:
        raise ValueError(f"events[{index}].time_range items must be HH:MM")

    anchored_raw = event["anchored_to"]
    if anchored_raw is None:
        anchored_to = None
    else:
        anchored_to = str(anchored_raw).strip() or None

    result = {
        "cluster_id": cluster_id,
        "display_name": display_name,
        "narrative_one_line": narrative_one_line,
        "event_count": event_count,
        "time_range": [start, end],
        "anchored_to": anchored_to,
    }
    if "merge_candidate_with" in event:
        merge_with = event["merge_candidate_with"]
        if not isinstance(merge_with, list):
            raise ValueError(f"events[{index}].merge_candidate_with must be array")
        result["merge_candidate_with"] = [str(item) for item in merge_with]
    if "peak_event_density" in event:
        result["peak_event_density"] = float(event["peak_event_density"] or 0.0)
    return result


def _validate_topic(topic: Any, index: int) -> dict[str, Any]:
    if not isinstance(topic, dict):
        raise ValueError(f"topics[{index}] must be object")
    keys = set(topic.keys())
    missing = sorted(_TOPIC_KEYS - keys)
    if missing:
        raise ValueError(f"topics[{index}] missing fields: {', '.join(missing)}")
    extra = sorted(keys - _TOPIC_KEYS - {"display", "title"})
    if extra:
        raise ValueError(f"topics[{index}] unexpected fields: {', '.join(extra)}")

    decisions_raw = topic["decisions"]
    shipped_raw = topic["shipped"]
    refs_raw = topic["events_ref"]
    if not isinstance(decisions_raw, list):
        raise ValueError(f"topics[{index}].decisions must be array")
    if not isinstance(shipped_raw, list):
        raise ValueError(f"topics[{index}].shipped must be array")
    if not isinstance(refs_raw, list):
        raise ValueError(f"topics[{index}].events_ref must be array")

    normalized = {
        "anchor": str(topic["anchor"]).strip(),
        "anchor_state": str(topic["anchor_state"]).strip(),
        "narrative": str(topic["narrative"]).strip(),
        "decisions": [str(item) for item in decisions_raw if str(item).strip()],
        "shipped": [str(item) for item in shipped_raw if str(item).strip()],
        "events_ref": [str(item) for item in refs_raw if str(item).strip()],
    }
    if "display" in topic:
        normalized["display"] = str(topic["display"]).strip()
    if "title" in topic:
        normalized["title"] = str(topic["title"]).strip()
    return normalized


def _legacy_cluster_to_event(cluster: dict[str, Any]) -> dict[str, Any]:
    output = {
        "cluster_id": str(cluster.get("slug") or "").strip(),
        "display_name": str(cluster.get("display_name") or "").strip(),
        "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
        "event_count": int(cluster.get("event_count") or 0),
        "time_range": list(cluster.get("time_range") or ["00:00", "23:59"]),
        "anchored_to": None,
        "merge_candidate_with": [str(item) for item in (cluster.get("merge_candidate_with") or [])],
    }
    if "peak_event_density" in cluster:
        output["peak_event_density"] = float(cluster.get("peak_event_density") or 0.0)
    return output


def _event_to_legacy_cluster(event: dict[str, Any]) -> dict[str, Any]:
    output = {
        "slug": str(event.get("cluster_id") or "").strip(),
        "display_name": str(event.get("display_name") or "").strip(),
        "narrative_one_line": str(event.get("narrative_one_line") or "").strip(),
        "event_count": int(event.get("event_count") or 0),
        "time_range": list(event.get("time_range") or ["00:00", "23:59"]),
        "merge_candidate_with": [str(item) for item in (event.get("merge_candidate_with") or [])],
    }
    if "peak_event_density" in event:
        output["peak_event_density"] = float(event.get("peak_event_density") or 0.0)
    return output


def _validate_cost(cost: Any) -> dict[str, Any]:
    if not isinstance(cost, dict):
        raise ValueError("cost must be object")

    keys = set(cost.keys())
    missing = sorted(_COST_KEYS - keys)
    extra = sorted(keys - _COST_KEYS)
    if missing:
        raise ValueError(f"cost missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"cost unexpected fields: {', '.join(extra)}")

    in_tokens = cost["in_tokens"]
    out_tokens = cost["out_tokens"]
    cost_usd = cost["cost_usd"]

    if not isinstance(in_tokens, int) or isinstance(in_tokens, bool):
        raise ValueError("cost.in_tokens must be integer")
    if not isinstance(out_tokens, int) or isinstance(out_tokens, bool):
        raise ValueError("cost.out_tokens must be integer")
    if not isinstance(cost_usd, (int, float)) or isinstance(cost_usd, bool):
        raise ValueError("cost.cost_usd must be number")

    return {
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "cost_usd": float(cost_usd),
    }


def _validate_summary_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("daily summary must be object")

    date_text = _validate_date(str(payload["date"]))
    events_raw = payload.get("events")
    clusters_raw = payload.get("clusters")
    if events_raw is None and clusters_raw is None:
        raise ValueError("daily summary missing fields: events/clusters")

    events: list[dict[str, Any]]
    if events_raw is not None:
        if not isinstance(events_raw, list):
            raise ValueError("events must be array")
        events = [_validate_event(event, index) for index, event in enumerate(events_raw)]
    else:
        if not isinstance(clusters_raw, list):
            raise ValueError("clusters must be array")
        clusters = [_validate_cluster(cluster, index) for index, cluster in enumerate(clusters_raw)]
        events = [_legacy_cluster_to_event(cluster) for cluster in clusters]

    topics_raw = payload.get("topics", [])
    if not isinstance(topics_raw, list):
        raise ValueError("topics must be array")
    topics = [_validate_topic(topic, index) for index, topic in enumerate(topics_raw)]

    unanchored_raw = payload.get("unanchored")
    if unanchored_raw is None:
        unanchored = [dict(event) for event in events if event.get("anchored_to") is None]
    else:
        if not isinstance(unanchored_raw, list):
            raise ValueError("unanchored must be array")
        unanchored = [_validate_event(event, index) for index, event in enumerate(unanchored_raw)]

    misc_raw = payload.get("misc_event_ids", [])
    if not isinstance(misc_raw, list):
        raise ValueError("misc_event_ids must be array")
    misc_event_ids = [str(item) for item in misc_raw if str(item).strip()]

    topic_status_snapshot = payload["topic_status_snapshot"]
    if not isinstance(topic_status_snapshot, dict):
        raise ValueError("topic_status_snapshot must be object")

    normalized = {
        "date": date_text,
        "topics": topics,
        "events": events,
        "unanchored": unanchored,
        "clusters": [_event_to_legacy_cluster(event) for event in events],
        "misc_event_ids": misc_event_ids,
        "topic_status_snapshot": topic_status_snapshot,
        "cost": _validate_cost(payload["cost"]),
    }
    return normalized


def write_daily_summary(
    date: str,
    clusters=None,
    misc=None,
    topic_snapshot=None,
    cost=None,
    *,
    topics=None,
    events=None,
    unanchored=None,
) -> Path:
    payload = {
        "date": date,
        "clusters": clusters if clusters is not None else [],
        "events": events,
        "topics": topics if topics is not None else [],
        "unanchored": unanchored,
        "misc_event_ids": misc if misc is not None else [],
        "topic_status_snapshot": topic_snapshot if topic_snapshot is not None else {},
        "cost": cost if cost is not None else {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0},
    }
    normalized = _validate_summary_payload(payload)

    target = _summary_dir() / f"{normalized['date']}.json"
    tmp_path = target.with_name(f"{target.name}.tmp")

    try:
        tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return target


def read_daily_summary(date: str) -> dict[str, Any] | None:
    date_text = _validate_date(date)
    target = _summary_dir() / f"{date_text}.json"
    if not target.exists():
        return None

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid daily summary JSON: {target}") from exc

    return _validate_summary_payload(payload)


def _anchor_link(anchor: str, display: str | None = None) -> str:
    display_text = str(display or "").strip()
    if display_text:
        return f"[[{anchor}|{display_text}]]"
    return f"[[{anchor}]]"


def _daily_event_cards(date_text: str) -> list[tuple[str, str]]:
    event_dir = get_data_dir() / "events" / date_text
    if not event_dir.exists():
        return []

    cards: list[tuple[str, str]] = []
    event_paths = sorted(
        (path for path in event_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in event_paths:
        slug = path.stem
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = _EVENT_H1_RE.search(markdown)
        title = match.group(1).strip() if match else slug
        cards.append((slug, title or slug))
    return cards


def _parse_event_card_selection(response: Any) -> list[str]:
    payload = response
    if isinstance(response, str):
        stripped = response.strip()
        if not stripped:
            return []
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return [item.strip() for item in re.split(r"[\n,，]+", stripped) if item.strip()]
    if isinstance(payload, dict):
        raw = payload.get("selected_slugs") or payload.get("slugs") or payload.get("selected")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            return [item.strip() for item in re.split(r"[\n,，]+", raw) if item.strip()]
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    return []


def filter_daily_event_cards(
    cards: list[tuple[str, str]],
    *,
    model_gateway: Any | None = None,
    min_count: int = 8,
    max_count: int = 15,
    fallback_count: int = 12,
) -> list[tuple[str, str]]:
    if not cards:
        return []

    lower = max(1, min(min_count, max_count))
    upper = max(lower, max_count)
    fallback_limit = min(max(fallback_count, lower), upper, len(cards))
    fallback = cards[:fallback_limit]
    if model_gateway is None or not hasattr(model_gateway, "call"):
        return fallback

    candidates = [{"slug": slug, "title": title} for slug, title in cards[:80]]
    prompt = "\n".join(
        [
            "从今天的事件卡中选出最值得放进 daily.md 的精选列表。",
            f"要求：保留 {lower}-{upper} 条，优先选择能代表主线、决策、产出、卡点的事件，去掉重复/噪声/低价值浏览。",
            "只输出 JSON：{\"selected_slugs\": [\"...\"]}。",
            json.dumps({"events": candidates}, ensure_ascii=False),
        ]
    )
    try:
        response = model_gateway.call(
            "daily_event_cards_filter",
            prompt,
            input_data={"events": candidates, "min_count": lower, "max_count": upper},
            retries=0,
        )
    except Exception as exc:
        logger.warning("daily event cards LLM filter fallback exc_type=%s exc=%s", type(exc).__name__, exc)
        return fallback

    by_slug = {slug: (slug, title) for slug, title in cards}
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for slug in _parse_event_card_selection(response):
        if slug in by_slug and slug not in seen:
            selected.append(by_slug[slug])
            seen.add(slug)
        if len(selected) >= upper:
            break

    if len(selected) < min(lower, len(cards)):
        for slug, title in cards:
            if slug in seen:
                continue
            selected.append((slug, title))
            seen.add(slug)
            if len(selected) >= min(lower, len(cards)):
                break
    return selected[:upper] or fallback


def _cross_day_continuations(
    *,
    date_text: str,
    snapshot: dict[str, Any],
    topic_lookup: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    previous_date = (date_cls.fromisoformat(date_text) - timedelta(days=1)).isoformat()
    continuations: list[tuple[str, str]] = []
    for slug, payload in snapshot.items():
        if not isinstance(payload, dict):
            continue
        evidence_dates = {str(item) for item in (payload.get("evidence_dates") or []) if str(item).strip()}
        last_seen = str(payload.get("last_seen_date") or "").strip()
        if previous_date not in evidence_dates or date_text not in evidence_dates and last_seen != date_text:
            continue
        topic = topic_lookup.get(str(slug))
        display = str(
            (topic or {}).get("display")
            or (topic or {}).get("title")
            or payload.get("name")
            or slug
        ).strip() or str(slug)
        anchor = str((topic or {}).get("anchor") or slug).strip() or str(slug)
        continuations.append((anchor, display))
    return continuations


def render_daily_markdown(
    *,
    date: str,
    clusters: list[dict[str, Any]] | None = None,
    topics: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    unanchored: list[dict[str, Any]] | None = None,
    previous_day_anchors: list[str] | None = None,
    narrative_markdown: str | None = None,
    topic_snapshot: dict[str, Any] | None = None,
    model_gateway: Any | None = None,
    event_cards: list[tuple[str, str]] | None = None,
) -> str:
    date_text = _validate_date(date)
    if topics is None and events is None:
        legacy_clusters = [cluster for cluster in (clusters or []) if isinstance(cluster, dict)]
        events = [_legacy_cluster_to_event(cluster) for cluster in legacy_clusters]
        topics = [
            {
                "anchor": str(cluster.get("slug") or "").strip(),
                "anchor_state": "continuing",
                "narrative": str(cluster.get("narrative_one_line") or "").strip(),
                "decisions": [],
                "shipped": [],
                "events_ref": [str(cluster.get("slug") or "").strip()],
                "display": str(cluster.get("display_name") or cluster.get("slug") or "").strip(),
            }
            for cluster in legacy_clusters
            if str(cluster.get("slug") or "").strip()
        ]
        unanchored = [event for event in (events or []) if event.get("anchored_to") is None]

    topic_list = [topic for topic in (topics or []) if isinstance(topic, dict)]
    event_list = [event for event in (events or []) if isinstance(event, dict)]
    previous = [str(item) for item in (previous_day_anchors or []) if str(item).strip()]
    snapshot = topic_snapshot if isinstance(topic_snapshot, dict) else {}

    def _extract_section(source: str, heading: str) -> str:
        if not source.strip():
            return ""
        lines = source.splitlines()
        start = -1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## ") and heading in stripped[3:]:
                start = idx + 1
                break
        if start < 0:
            return ""
        collected: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            if stripped.startswith("## "):
                break
            collected.append(line)
        return "\n".join(collected).strip()

    source_markdown = str(narrative_markdown or "")
    highlight_section = _extract_section(source_markdown, "今日要点")

    lines = ["📍 Asia/Shanghai", "", f"# {date_text}", "", "## 今日要点", ""]
    if highlight_section:
        lines.append(highlight_section)
    elif topic_list:
        lines.append(str(topic_list[0].get("narrative") or "").strip() or "—")
    else:
        lines.append("—")

    lines.extend(["", "## 今天做的事", ""])
    event_lookup = {str(item.get("cluster_id") or ""): item for item in event_list}
    for topic in topic_list:
        anchor = str(topic.get("anchor") or "").strip()
        display = str(topic.get("display") or topic.get("title") or anchor).strip() or anchor
        heading = _anchor_link(anchor, display) if anchor else display
        lines.append(f"### {heading}")
        lines.append("")
        refs = [str(item) for item in (topic.get("events_ref") or []) if str(item).strip()]
        narrative = str(topic.get("narrative") or "").strip()
        if not narrative and refs:
            narrative = "；".join(
                str((event_lookup.get(ref) or {}).get("narrative_one_line") or "").strip()
                for ref in refs
                if str((event_lookup.get(ref) or {}).get("narrative_one_line") or "").strip()
            )
        lines.append(narrative or "—")
        if refs:
            lines.append("")
            for ref in refs:
                event_payload = event_lookup.get(ref) or {}
                event_line = str(event_payload.get("narrative_one_line") or "").strip()
                event_title = str(event_payload.get("display_name") or ref).strip() or ref
                lines.append(f"- {event_title}: {event_line or '—'}")
        lines.append("")
    if not topic_list:
        lines.extend(["—", ""])

    raw_event_cards = event_cards if event_cards is not None else _daily_event_cards(date_text)
    selected_event_cards = filter_daily_event_cards(raw_event_cards, model_gateway=model_gateway)
    if selected_event_cards:
        lines.extend(["## 今天的事件卡", ""])
        for slug, title in selected_event_cards:
            lines.append(f"- [[../.keypulse/events/{date_text}/{slug}|{title}]]")

    lines.extend(["", "## 跨日延续"])

    blocked_topics = [
        topic
        for topic in topic_list
        if str(topic.get("anchor_state") or "").strip() == "blocked"
    ]
    if blocked_topics:
        lines.extend(["", "## 今天的卡点", ""])
        for topic in blocked_topics:
            anchor = str(topic.get("anchor") or "").strip()
            display = str(topic.get("display") or topic.get("title") or anchor).strip() or anchor
            narrative = str(topic.get("narrative") or "").strip()
            suffix = f": {narrative}" if narrative else ""
            lines.append(f"- {_anchor_link(anchor, display)}{suffix}")

    lines.extend(["", "## 明日的锚点", "", "> 明天我想：______", ">", "> _写一句话留给明天的自己_"])

    if event_list and not topic_list:
        lines.extend(["", "<!-- events_count: {} -->".format(len(event_list))])
    lines.append("")
    return "\n".join(lines).strip()
