from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"\[(DECISION|SHIPPED|BLOCKED):\s*(.+?)\]", re.IGNORECASE)


def _collect_daily_text(topic_slug: str, topic_name: str, daily_summaries: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    name_lower = topic_name.lower()
    for daily in daily_summaries:
        if not isinstance(daily, dict):
            continue
        date_text = str(daily.get("date") or "").strip()
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            display = str(cluster.get("display_name") or "").strip().lower()
            if slug != topic_slug and topic_slug not in display and name_lower not in display:
                continue
            narrative = str(cluster.get("narrative_one_line") or "").strip()
            if narrative:
                rows.append((date_text, narrative))
    return rows


def _extract_tagged_items(topic_slug: str, topic_name: str, daily_summaries: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    decisions: list[str] = []
    shipped: list[str] = []
    blocked: list[str] = []
    name_lower = topic_name.lower()

    for daily in daily_summaries:
        if not isinstance(daily, dict):
            continue
        text = str(daily.get("content_full") or "")
        if not text:
            lines: list[str] = []
            for cluster in daily.get("clusters") or []:
                if isinstance(cluster, dict):
                    lines.append(str(cluster.get("narrative_one_line") or ""))
            text = "\n".join(lines)

        if topic_slug not in text and name_lower not in text.lower():
            continue

        for match in _TAG_RE.finditer(text):
            kind = match.group(1).upper()
            value = match.group(2).strip()
            if not value:
                continue
            if kind == "DECISION":
                decisions.append(value)
            elif kind == "SHIPPED":
                shipped.append(value)
            elif kind == "BLOCKED":
                blocked.append(value)

    return decisions, shipped, blocked


def generate_degraded_topic(
    topic: dict[str, Any],
    daily_summaries: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    slug = str(topic.get("slug") or "").strip()
    name = str(topic.get("name") or slug).strip()

    rows = _collect_daily_text(slug, name, daily_summaries)
    decisions, shipped, blocked = _extract_tagged_items(slug, name, daily_summaries)

    anchor = "[[2026-01-01]]"
    if rows and rows[0][0]:
        anchor = f"[[{rows[0][0]}]]"

    narrative_lines: list[str] = []
    for date_text, line in rows[:3]:
        date_part = f"[[{date_text}]] " if date_text else ""
        narrative_lines.append(f"{date_part}{line}".strip())

    if not narrative_lines:
        entries = [entry for entry in (topic.get("weekly_entries") or []) if isinstance(entry, dict)]
        for entry in entries[:3]:
            date_text = str(entry.get("date") or "").strip()
            one_line = str(entry.get("narrative_one_line") or "").strip()
            if not one_line:
                continue
            date_part = f"[[{date_text}]] " if date_text else ""
            narrative_lines.append(f"{date_part}{one_line}".strip())

    narrative = "\n".join(narrative_lines).strip()
    if not narrative:
        narrative = f"{anchor} {name} 本周有真实记录但模型失败，已按日报证据回填。"

    return {
        "slug": slug,
        "narrative": f"### {name}\n{narrative}",
        "anchors": [anchor],
        "decisions": decisions[:3],
        "outputs": shipped[:3],
        "blockers": ([f"LLM 失败: {reason}"] if reason else []) + blocked[:2],
        "quality_status": "degraded_from_daily",
    }
