from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any


def _as_text(event: dict[str, Any]) -> str:
    return str(
        event.get("content_text")
        or event.get("body")
        or event.get("window_title")
        or event.get("title")
        or ""
    ).strip()


def _split_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _extract_tags(event: dict[str, Any]) -> list[str]:
    direct_tags = _split_tags(event.get("tags"))
    if direct_tags:
        return direct_tags
    metadata_json = event.get("metadata_json")
    if not metadata_json:
        return []
    try:
        metadata = json.loads(str(metadata_json))
    except Exception:
        return []
    return _split_tags(metadata.get("tags"))


def _normalize_key(event: dict[str, Any]) -> str:
    text = " ".join(_as_text(event).lower().split())
    if text:
        return text[:80]
    return str(event.get("topic_key") or event.get("event_type") or event.get("source") or "event")


def _filter_reason(event: dict[str, Any]) -> str | None:
    source = str(event.get("source") or "")
    event_type = str(event.get("event_type") or "")
    app_name = str(event.get("app_name") or "").strip()
    window_title = str(event.get("window_title") or "").strip()
    text = _as_text(event)

    if source == "idle" or event_type.startswith("idle_"):
        return "idle_event"

    if source not in {"manual", "clipboard"}:
        if app_name and window_title and app_name == window_title and len(window_title) <= 12 and len(text) <= 12:
            return "low_signal_window"

    if source not in {"manual", "clipboard"} and len(text) < 8:
        return "low_density_fragment"

    return None


def _score_candidate(event: dict[str, Any], recurrence_count: int) -> tuple[float, dict[str, float]]:
    source = str(event.get("source") or "")
    text = _as_text(event)
    tags = _extract_tags(event)
    topic_key = str(event.get("topic_key") or "").strip()
    lowered = text.lower()

    explicitness = 1.0 if source == "manual" else 0.85 if source == "clipboard" else 0.35
    novelty = 0.65 if recurrence_count <= 1 else 0.35 if recurrence_count == 2 else 0.15
    reusable_keywords = ("方法", "原则", "流程", "模板", "策略", "rule", "pattern", "template", "workflow")
    reusability = 0.45 if topic_key or tags or any(token in lowered for token in reusable_keywords) else 0.0
    decision_keywords = ("决定", "选择", "约束", "取舍", "优先", "方案", "should", "must", "decide", "tradeoff")
    decision_signal = 0.45 if any(token in lowered for token in decision_keywords) else 0.0
    density = 0.55 if len(text) >= 90 else 0.4 if len(text) >= 48 else 0.2 if len(text) >= 20 else 0.0
    recurrence = 0.55 if recurrence_count >= 3 else 0.35 if recurrence_count == 2 else 0.0

    breakdown = {
        "explicitness": explicitness,
        "novelty": novelty,
        "reusability": reusability,
        "decision_signal": decision_signal,
        "density": density,
        "recurrence": recurrence,
    }
    return round(sum(breakdown.values()), 4), breakdown


def translate_why_selected(why_selected: dict[str, float], metadata: dict[str, Any] | None = None) -> list[str]:
    metadata = metadata or {}
    recurrence_count = int(metadata.get("recurrence_count") or 0)
    labels: list[str] = []

    explicitness = float(why_selected.get("explicitness") or 0.0)
    novelty = float(why_selected.get("novelty") or 0.0)
    recurrence = float(why_selected.get("recurrence") or 0.0)
    decision_signal = float(why_selected.get("decision_signal") or 0.0)
    density = float(why_selected.get("density") or 0.0)
    reusability = float(why_selected.get("reusability") or 0.0)

    if explicitness >= 0.25:
        labels.append("你主动保存")
    if novelty >= 0.6:
        labels.append("今日首次出现")
    if recurrence >= 0.5 and recurrence_count > 0:
        labels.append(f"最近第 {recurrence_count} 次提及")
    if decision_signal >= 0.4:
        labels.append("含决策信号")
    if density >= 0.5:
        labels.append("内容密度高")
    if reusability >= 0.4:
        labels.append("可复用")

    if not labels and metadata.get("source"):
        labels.append(str(metadata["source"]))
    return labels[:2]


def build_surface_snapshot(
    events: list[dict[str, Any]] | None = None,
    top_k: int = 10,
    *,
    daily_draft: Any | None = None,
    theme_summary: Any | None = None,
) -> dict[str, Any]:
    if events is None:
        return {
            "daily": asdict(daily_draft) if daily_draft is not None and hasattr(daily_draft, "__dataclass_fields__") else daily_draft,
            "themes": asdict(theme_summary) if theme_summary is not None and hasattr(theme_summary, "__dataclass_fields__") else theme_summary,
        }

    filtered_reasons: Counter[str] = Counter()
    survivors: list[dict[str, Any]] = []

    for event in events:
        if event is None:
            continue
        reason = _filter_reason(event)
        if reason:
            filtered_reasons[reason] += 1
            continue
        survivors.append(event)

    deduped_survivors: list[dict[str, Any]] = []
    deduped_by_hash: dict[tuple[str, str], dict[str, Any]] = {}
    for event in survivors:
        source = str(event.get("source") or "")
        content_hash = str(event.get("content_hash") or "")
        if source in {"manual", "clipboard"} and content_hash:
            key = (source, content_hash)
            current = deduped_by_hash.get(key)
            if current is None or str(event.get("ts_start") or "") < str(current.get("ts_start") or ""):
                deduped_by_hash[key] = event
            continue
        deduped_survivors.append(event)
    survivors = deduped_survivors + list(deduped_by_hash.values())

    recurrence_counts = Counter(_normalize_key(event) for event in survivors)
    candidates: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in survivors:
        recurrence_count = recurrence_counts[_normalize_key(event)]
        score, breakdown = _score_candidate(event, recurrence_count)
        text = _as_text(event)
        title = str(event.get("title") or event.get("window_title") or event.get("app_name") or text[:72] or event.get("event_type") or "event").strip()
        tags = _extract_tags(event)
        topic_key = str(event.get("topic_key") or "").strip()
        title = str(
            event.get("title")
            or event.get("window_title")
            or text[:72]
            or event.get("app_name")
            or event.get("event_type")
            or "event"
        ).strip()
        candidate = {
            "score": score,
            "title": title,
            "source": str(event.get("source") or ""),
            "event_type": str(event.get("event_type") or ""),
            "topic_key": topic_key or None,
            "tags": tags,
            "evidence": text,
            "why_selected": breakdown,
            "why_labels": translate_why_selected(
                breakdown,
                {
                    "source": str(event.get("source") or ""),
                    "recurrence_count": recurrence_count,
                    "topic_key": topic_key,
                    "title": title,
                },
            ),
        }
        candidates.append(candidate)

        if topic_key:
            grouped[f"topic:{topic_key}"].append(candidate)
        else:
            for tag in tags:
                grouped[f"tag:{tag}"].append(candidate)

    candidates.sort(key=lambda item: (-float(item["score"]), item["title"].lower()))
    top_candidates = candidates[:top_k]

    theme_candidates: list[dict[str, Any]] = []
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: (-float(item["score"]), item["title"].lower()))
        theme_candidates.append(
            {
                "key": key,
                "topic_key": key.removeprefix("topic:").removeprefix("tag:"),
                "item_count": len(items),
                "avg_score": round(sum(float(item["score"]) for item in items) / len(items), 4),
                "top_evidence": ordered[0]["evidence"],
            }
        )
    theme_candidates.sort(key=lambda item: (-int(item["item_count"]), -float(item["avg_score"]), item["key"]))

    return {
        "filtered_total": sum(filtered_reasons.values()),
        "filtered_reasons": dict(filtered_reasons),
        "candidates": top_candidates,
        "theme_candidates": theme_candidates,
    }
