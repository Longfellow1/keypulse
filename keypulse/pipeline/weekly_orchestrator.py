from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keypulse.config import Config
from keypulse.integrations import resolve_active_sink
from keypulse.pipeline.daily_summary import read_daily_summary
from keypulse.pipeline.model import LLMCallError, ModelGateway, load_model_gateway
from keypulse.pipeline.topic_status import TopicStatusSnapshot, compute_topic_status
from keypulse.prompts.loader import load_prompt
from keypulse.store.repository import set_state
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.paths import get_data_dir


_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"
_TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_ENTRY_DATE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\b")
_ENTRY_EVENTS_RE = re.compile(r"\|\s*(\d+)\s+events?\s*\|", re.IGNORECASE)
_RELATED_EVENT_ID_RE = re.compile(r"\|(.*?)\]\]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,29}")

_STATUS_PRIORITY = {
    "new": 0,
    "accelerating": 1,
    "revived": 2,
    "ongoing": 3,
    "steady": 4,
    "declining": 5,
}


class WeeklyOrchestratorError(RuntimeError):
    """Raised when weekly orchestration cannot complete."""


@dataclass(frozen=True)
class CostSnapshot:
    total_cost_usd: float
    calls: int
    cache_hits: int


def _week_start(week_str: str) -> date_cls:
    text = str(week_str or "").strip()
    if "-W" not in text:
        raise ValueError(f"invalid ISO week: {week_str}")
    year_text, week_text = text.split("-W", 1)
    try:
        return date_cls.fromisocalendar(int(year_text), int(week_text), 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: {week_str}") from exc


def _week_dates(week_str: str) -> list[str]:
    start = _week_start(week_str)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def _build_prompt(spec_body: str, capability: str, payload: dict[str, Any]) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join([f"CAPABILITY: {capability}", spec_body.strip(), _INPUT_MARKER_BEGIN, rendered, _INPUT_MARKER_END])


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


def _parse_cost_ts(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _weekly_cost_window(week_str: str) -> CostSnapshot:
    start_date = _week_start(week_str)
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    path = get_data_dir() / "cost.jsonl"
    if not path.exists():
        return CostSnapshot(total_cost_usd=0.0, calls=0, cache_hits=0)

    total = 0.0
    calls = 0
    cache_hits = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = _parse_cost_ts(str(row.get("ts") or ""))
            if ts is None or not (start <= ts < end):
                continue
            calls += 1
            total += float(row.get("cost_usd") or 0.0)
            if bool(row.get("cache_hit")):
                cache_hits += 1
    return CostSnapshot(total_cost_usd=round(total, 8), calls=calls, cache_hits=cache_hits)


def _load_topic_index() -> list[dict[str, Any]]:
    topics_dir = get_data_dir() / "topics"
    if not topics_dir.exists():
        return []

    result: list[dict[str, Any]] = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        slug = topic_path.stem
        if not _TOPIC_RE.fullmatch(slug):
            continue
        text = topic_path.read_text(encoding="utf-8")

        display_name = slug
        keywords: list[str] = []
        first_seen = ""
        last_seen = ""
        pinned = False
        entry_dates: list[str] = []
        entry_rows: list[dict[str, Any]] = []
        related_event_ids: list[str] = []

        lines = text.splitlines()
        in_frontmatter = False
        in_keywords = False
        in_entries = False
        in_related = False

        if lines and lines[0].strip() == "---":
            in_frontmatter = True

        for idx, line in enumerate(lines):
            stripped = line.strip()

            if in_frontmatter:
                if idx == 0:
                    continue
                if stripped == "---":
                    in_frontmatter = False
                    in_keywords = False
                    continue
                if stripped.startswith("display_name:"):
                    display_name = stripped.split(":", 1)[1].strip() or display_name
                    continue
                if stripped.startswith("first_seen:"):
                    first_seen = stripped.split(":", 1)[1].strip()
                    continue
                if stripped.startswith("last_seen:"):
                    last_seen = stripped.split(":", 1)[1].strip()
                    continue
                if stripped.startswith("pinned:"):
                    pinned = stripped.split(":", 1)[1].strip().lower() == "true"
                    continue
                if stripped.startswith("keywords:"):
                    inline = stripped.split(":", 1)[1].strip()
                    if inline.startswith("[") and inline.endswith("]"):
                        for item in inline.strip("[]").split(","):
                            token = item.strip().strip('"').strip("'").lower()
                            if token:
                                keywords.append(token)
                        in_keywords = False
                    else:
                        in_keywords = True
                    continue
                if in_keywords and stripped.startswith("-"):
                    token = stripped.lstrip("-").strip().strip('"').strip("'").lower()
                    if token:
                        keywords.append(token)
                    continue
                in_keywords = False
                continue

            if stripped == "## Entries":
                in_entries = True
                in_related = False
                continue
            if stripped == "## Related Events":
                in_related = True
                in_entries = False
                continue
            if stripped.startswith("## "):
                in_entries = False
                in_related = False
                continue

            if in_entries:
                matched = _ENTRY_DATE_RE.match(stripped)
                if matched:
                    entry_date = matched.group(1)
                    entry_dates.append(entry_date)
                    events_match = _ENTRY_EVENTS_RE.search(stripped)
                    event_count = int(events_match.group(1)) if events_match else 1
                    entry_rows.append({"date": entry_date, "event_count": max(event_count, 1)})
                continue

            if in_related and stripped.startswith("-"):
                match_id = _RELATED_EVENT_ID_RE.search(stripped)
                if match_id:
                    value = match_id.group(1).strip()
                    if value:
                        related_event_ids.append(value)

        dedup_keywords: list[str] = []
        seen_kw: set[str] = set()
        for item in keywords:
            token = str(item).strip().lower()
            if not token or token in seen_kw:
                continue
            seen_kw.add(token)
            dedup_keywords.append(token)

        result.append(
            {
                "slug": slug,
                "display_name": display_name,
                "keywords": dedup_keywords[:10],
                "first_seen": first_seen or None,
                "last_seen": last_seen or None,
                "entry_dates": sorted(entry_dates),
                "entry_rows": sorted(entry_rows, key=lambda item: str(item.get("date") or "")),
                "related_event_ids": sorted(set(related_event_ids)),
                "pinned": pinned,
            }
        )
    return result


def _build_weekly_history(topics_index: list[dict[str, Any]], week_str: str) -> dict[str, list[int]]:
    start = _week_start(week_str)
    history: dict[str, list[int]] = {}
    for topic in topics_index:
        slug = str(topic.get("slug") or "").strip()
        if not slug:
            continue
        counts_by_week: dict[tuple[int, int], int] = {}
        entry_rows = topic.get("entry_rows")
        if isinstance(entry_rows, list):
            iter_rows = entry_rows
        else:
            iter_rows = [{"date": value, "event_count": 1} for value in (topic.get("entry_dates") or [])]
        for row in iter_rows:
            if not isinstance(row, dict):
                continue
            date_text = str(row.get("date") or "")
            try:
                day = date_cls.fromisoformat(str(date_text))
            except ValueError:
                continue
            if day >= start:
                continue
            iso = day.isocalendar()
            key = (iso.year, iso.week)
            raw_count = row.get("event_count")
            if isinstance(raw_count, bool):
                continue
            if isinstance(raw_count, int):
                event_count = raw_count
            else:
                try:
                    event_count = int(str(raw_count))
                except (TypeError, ValueError):
                    event_count = 1
            if event_count <= 0:
                continue
            counts_by_week[key] = counts_by_week.get(key, 0) + event_count

        ordered = [counts_by_week[key] for key in sorted(counts_by_week.keys())]
        history[slug] = ordered
    return history


def _load_week_daily_summaries(week_str: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for day in _week_dates(week_str):
        payload = read_daily_summary(day)
        if payload is not None:
            summaries.append(payload)
    return summaries


def _collect_topic_week_entries(daily_summaries: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    mapping: dict[str, list[dict[str, str]]] = {}
    for daily in daily_summaries:
        date = str(daily.get("date") or "")
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug or slug == "misc":
                continue
            mapping.setdefault(slug, []).append(
                {
                    "date": date,
                    "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
                }
            )
    return mapping


def _collect_merge_candidate_pairs(daily_summaries: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for daily in daily_summaries:
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug:
                continue
            for item in cluster.get("merge_candidate_with") or []:
                other = str(item or "").strip()
                if not other or other == slug:
                    continue
                pair = tuple(sorted((slug, other)))
                pairs.add(pair)
    return sorted(pairs)


def _keywords(tokens: list[str], text: str) -> set[str]:
    result = set(tokens)
    result.update(_WORD_RE.findall(text.lower()))
    return {item for item in result if item}


def _topic_map(topics_index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("slug")): dict(item) for item in topics_index if str(item.get("slug") or "").strip()}


def _affinity_score(topic_a: dict[str, Any], topic_b: dict[str, Any], week_entries: dict[str, list[dict[str, str]]]) -> float:
    kw_a = set(str(item).lower() for item in (topic_a.get("keywords") or []))
    kw_b = set(str(item).lower() for item in (topic_b.get("keywords") or []))
    keyword_overlap = len(kw_a & kw_b) * 3.0

    ent_a = _keywords(list(kw_a), " ".join([str(topic_a.get("display_name") or ""), str(topic_a.get("slug") or "")]))
    ent_b = _keywords(list(kw_b), " ".join([str(topic_b.get("display_name") or ""), str(topic_b.get("slug") or "")]))
    shared_entities = len(ent_a & ent_b) * 2.5

    ev_a = set(str(item) for item in (topic_a.get("related_event_ids") or []))
    ev_b = set(str(item) for item in (topic_b.get("related_event_ids") or []))
    shared_events = len(ev_a & ev_b) * 1.5

    days_a = {str(item.get("date") or "") for item in week_entries.get(str(topic_a.get("slug")), [])}
    days_b = {str(item.get("date") or "") for item in week_entries.get(str(topic_b.get("slug")), [])}
    shared_days = len(days_a & days_b) * 1.0

    return round(keyword_overlap + shared_entities + shared_events + shared_days, 3)


def _topic_summary_for_l4(topic: dict[str, Any], week_entries: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    slug = str(topic.get("slug") or "")
    return {
        "display_name": str(topic.get("display_name") or slug),
        "keywords": [str(item) for item in (topic.get("keywords") or [])],
        "weekly_entries": list(week_entries.get(slug, [])),
    }


def _parse_markdown_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("-"):
            out.append(stripped)
    return out


def _append_section_lines(text: str, heading: str, lines_to_add: list[str]) -> str:
    lines = text.splitlines()
    start = None
    end = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        lines.extend(["", heading])
        start = len(lines)

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].strip().startswith("## "):
            end = idx
            break

    current = [lines[idx].strip() for idx in range(start, end) if lines[idx].strip().startswith("-")]
    merged = list(current)
    for line in lines_to_add:
        if line not in merged:
            merged.append(line)

    new_lines = lines[:start] + [item for item in merged] + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"


def _merge_topic_files(into_slug: str, from_slug: str) -> bool:
    topics_dir = get_data_dir() / "topics"
    into_path = topics_dir / f"{into_slug}.md"
    from_path = topics_dir / f"{from_slug}.md"
    if not into_path.exists() or not from_path.exists():
        return False

    into_text = into_path.read_text(encoding="utf-8")
    from_text = from_path.read_text(encoding="utf-8")

    from_entries = _parse_markdown_lines(from_text, "## Entries")
    from_related = _parse_markdown_lines(from_text, "## Related Events")

    merged_text = _append_section_lines(into_text, "## Entries", from_entries)
    merged_text = _append_section_lines(merged_text, "## Related Events", from_related)

    atomic_write_text(into_path, merged_text)
    from_path.unlink()
    return True


def _apply_reconcile_decisions(decisions: list[dict[str, Any]]) -> list[str]:
    merged: list[str] = []
    for item in decisions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if action != "merge":
            continue
        into_slug = str(item.get("into") or "").strip()
        slug_a = str(item.get("slug_a") or "").strip()
        slug_b = str(item.get("slug_b") or "").strip()
        if not into_slug or not slug_a or not slug_b:
            continue
        from_slug = slug_b if into_slug == slug_a else slug_a
        if _merge_topic_files(into_slug, from_slug):
            merged.append(f"{from_slug}->{into_slug}")
    return merged


def _status_badge(status: str, lifecycle: str) -> str:
    mapping = {
        "new": "新冒头",
        "accelerating": "★加速",
        "revived": "沉睡复活",
        "ongoing": "持续主线",
        "steady": "持平",
        "declining": "退潮",
    }
    if lifecycle == "dormant":
        return "休眠"
    return mapping.get(status, status)


def _build_main_section_lines(
    top_topics: list[TopicStatusSnapshot],
    topic_index_map: dict[str, dict[str, Any]],
    l5_output: dict[str, Any],
) -> list[str]:
    narratives = l5_output.get("narratives") if isinstance(l5_output, dict) else []
    by_slug: dict[str, dict[str, Any]] = {}
    if isinstance(narratives, list):
        for item in narratives:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            if slug:
                by_slug[slug] = item

    lines = ["## 这周的主线", ""]
    for topic in top_topics:
        details = topic_index_map.get(topic.slug, {})
        display = str(details.get("display_name") or topic.display_name)
        lines.append(f"### {display} ({_status_badge(topic.status, topic.lifecycle_status)})")

        payload = by_slug.get(topic.slug, {})
        narrative = str(payload.get("narrative") or "").strip()
        anchors = payload.get("anchors") if isinstance(payload.get("anchors"), list) else []
        if not narrative:
            fallback_anchor = anchors[0] if anchors else ""
            narrative = f"本周{display}延续已有节奏。{fallback_anchor}".strip()
        lines.append(narrative)
        lines.append("")
    if len(lines) == 2:
        lines.append("- （本周无可写主题）")
        lines.append("")
    return lines


def _build_explorer_section_lines(l6_output: dict[str, Any]) -> list[str]:
    missed = l6_output.get("missed_balls") if isinstance(l6_output, dict) else []
    observation = l6_output.get("observation") if isinstance(l6_output, dict) else {}

    lines = ["## 这周的回声", "", "### 没接住的球"]
    if isinstance(missed, list) and missed:
        for item in missed[:3]:
            if not isinstance(item, dict):
                continue
            what = str(item.get("what") or "").strip()
            anchor = str(item.get("anchor_link") or "").strip()
            if what:
                lines.append(f"- {what}{(' ' + anchor) if anchor else ''}")
    else:
        lines.append("- （无）")

    lines.extend(["", "### 一个观察"])
    if isinstance(observation, dict):
        text = str(observation.get("text") or "").strip()
        anchor = str(observation.get("anchor_link") or "").strip()
        quote = str(observation.get("anchor_quote") or "").strip()
        if text:
            lines.append(f"{text}{(' ' + anchor) if anchor else ''}")
            if quote:
                lines.append(f"> 证据: {quote}")
        else:
            lines.append("本周笔友没看见值得问的事。")
    else:
        lines.append("本周笔友没看见值得问的事。")

    lines.extend(["", "> [!note] 我的批注", "> (空,M2 写回长期记忆)", ""])
    return lines


def _render_weekly_markdown(
    week_str: str,
    top_topics: list[TopicStatusSnapshot],
    topic_index_map: dict[str, dict[str, Any]],
    l5_output: dict[str, Any],
    l6_output: dict[str, Any],
) -> str:
    cost = _weekly_cost_window(week_str)
    lines = [
        f"# 这周 ({week_str})",
        "",
        f"> 本期成本: ${cost.total_cost_usd:.2f} · 调用 {cost.calls} 次 · cache hit {cost.cache_hits} 次",
        "",
    ]
    lines.extend(_build_main_section_lines(top_topics, topic_index_map, l5_output))
    lines.extend(_build_explorer_section_lines(l6_output))
    return "\n".join(lines)


def _append_log(record: dict[str, Any]) -> None:
    log_path = get_data_dir() / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _weekly_path(week_str: str) -> Path:
    sink = resolve_active_sink(Config.load(), persist=False)
    path = sink.output_dir / "Weekly" / f"{week_str}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _mock_l4_output(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = []
    for pair in payload.get("candidate_pairs") or []:
        if not isinstance(pair, dict):
            continue
        slug_a = str(pair.get("slug_a") or "").strip()
        slug_b = str(pair.get("slug_b") or "").strip()
        score = float(pair.get("affinity_score") or 0.0)
        if slug_a and slug_b and score >= 5.0:
            decisions.append(
                {
                    "slug_a": slug_a,
                    "slug_b": slug_b,
                    "action": "merge",
                    "into": slug_a,
                    "reason": "affinity high",
                }
            )
        elif slug_a and slug_b:
            decisions.append(
                {
                    "slug_a": slug_a,
                    "slug_b": slug_b,
                    "action": "keep_separate",
                    "reason": "affinity low",
                }
            )
    return {"decisions": decisions}


def _mock_l5_output(payload: dict[str, Any]) -> dict[str, Any]:
    narratives = []
    for topic in payload.get("topics_to_write") or []:
        if not isinstance(topic, dict):
            continue
        slug = str(topic.get("slug") or "").strip()
        display = str(topic.get("display_name") or slug).strip()
        entries = topic.get("weekly_entries") or []
        first = entries[0]["date"] if entries else "2026-01-01"
        status = str(topic.get("status") or "steady")
        suffix = {
            "new": "第一次集中出现",
            "accelerating": "这周更频繁地推进",
            "declining": "这周明显少了",
            "revived": "沉寂后又重新出现",
            "ongoing": "这条线仍在持续",
        }.get(status, "节奏基本持平")
        narratives.append(
            {
                "slug": slug,
                "narrative": f"本周{display}{suffix}，关键动作集中在 {first} 附近并形成连续推进。 [[{first}]]",
                "anchors": [f"[[{first}]]"],
            }
        )
    return {"narratives": narratives}


def _mock_l6_output(payload: dict[str, Any]) -> dict[str, Any]:
    dailies = payload.get("weekly_dailies") or []
    first_day = dailies[0]["date"] if dailies else "2026-01-01"
    first_body = str(dailies[0].get("content_full") or "") if dailies else ""
    quote = first_body[:60] if first_body else "本周持续推进同一主线"
    return {
        "missed_balls": [
            {"what": "周一你说想收敛主线，后面没看到", "anchor_link": f"[[{first_day}]]"}
        ],
        "observation": {
            "text": "同一条线多次返工，是否可以再拆小一步?",
            "anchor_link": f"[[{first_day}]]",
            "anchor_quote": quote,
        },
    }


def _install_mock_backend(gateway: ModelGateway) -> None:
    fail_budget = int(os.environ.get("MOCK_LLM_FAILS", "0") or 0)
    delay_sec = float(os.environ.get("MOCK_WEEKLY_DELAY_SEC", "0") or 0.0)
    fail_counter = {"count": 0}

    def fake_call_capability_backend(**kwargs):
        if fail_counter["count"] < fail_budget:
            fail_counter["count"] += 1
            raise RuntimeError("mock llm forced failure")

        if delay_sec > 0:
            capability = str(kwargs.get("prompt") or "").splitlines()[0]
            if "L5_weekly_main_narrative" in capability or "L6_explorer" in capability:
                time.sleep(delay_sec)

        prompt = str(kwargs.get("prompt") or "")
        capability, payload = _extract_prompt_payload(prompt)
        if capability == "L4_weekly_reconcile":
            body = json.dumps(_mock_l4_output(payload), ensure_ascii=False)
        elif capability == "L5_weekly_main_narrative":
            body = json.dumps(_mock_l5_output(payload), ensure_ascii=False)
        elif capability == "L6_explorer":
            body = json.dumps(_mock_l6_output(payload), ensure_ascii=False)
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


def _status_for_prompt(topic: TopicStatusSnapshot) -> str:
    if topic.status in {"new", "accelerating", "steady", "declining", "revived", "ongoing"}:
        return topic.status
    if topic.lifecycle_status == "emerging":
        return "new"
    return "steady"


def _set_weekly_notice(week_str: str, message: str) -> None:
    payload = {"week": week_str, "message": message}
    set_state("weekly_notice", json.dumps(payload, ensure_ascii=False))


def _clear_weekly_notice() -> None:
    set_state("weekly_notice", "")


def run_weekly(week_str: str) -> str:
    run_started = datetime.now(timezone.utc)
    daily_summaries = _load_week_daily_summaries(week_str)

    if len(daily_summaries) < 5:
        _set_weekly_notice(week_str, "本周数据不足，周报跳过")
        _append_log(
            {
                "ts": run_started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "capability": "weekly_orchestrator",
                "week": week_str,
                "decision": "skip_low_daily_count",
                "daily_count": len(daily_summaries),
            }
        )
        return ""

    _clear_weekly_notice()

    topics_index = _load_topic_index()
    topic_map = _topic_map(topics_index)
    weekly_history = _build_weekly_history(topics_index, week_str)
    topic_status_map = compute_topic_status(
        topics_index=topics_index,
        daily_summaries=daily_summaries,
        week_str=week_str,
        weekly_history=weekly_history,
    )

    week_entries = _collect_topic_week_entries(daily_summaries)
    raw_pairs = _collect_merge_candidate_pairs(daily_summaries)
    scored_pairs: list[dict[str, Any]] = []
    for slug_a, slug_b in raw_pairs:
        topic_a = topic_map.get(slug_a)
        topic_b = topic_map.get(slug_b)
        if not topic_a or not topic_b:
            continue
        score = _affinity_score(topic_a, topic_b, week_entries)
        if score < 5.0:
            continue
        scored_pairs.append(
            {
                "slug_a": slug_a,
                "slug_b": slug_b,
                "affinity_score": score,
                "topic_a_summary": _topic_summary_for_l4(topic_a, week_entries),
                "topic_b_summary": _topic_summary_for_l4(topic_b, week_entries),
            }
        )

    gateway = _load_gateway()

    l4_decisions: list[dict[str, Any]] = []
    if scored_pairs:
        l4_input = {"scope_week": week_str, "candidate_pairs": scored_pairs}
        l4_spec = load_prompt("L4_weekly_reconcile")
        l4_prompt = _build_prompt(l4_spec.body, "L4_weekly_reconcile", l4_input)
        l4_output = gateway.call("L4_weekly_reconcile", l4_prompt, input_data=l4_input)
        if isinstance(l4_output, dict):
            maybe = l4_output.get("decisions")
            if isinstance(maybe, list):
                l4_decisions = [item for item in maybe if isinstance(item, dict)]

    merged_pairs = _apply_reconcile_decisions(l4_decisions)

    remaining_topics = _load_topic_index()
    remaining_status = compute_topic_status(
        topics_index=remaining_topics,
        daily_summaries=daily_summaries,
        week_str=week_str,
        weekly_history=_build_weekly_history(remaining_topics, week_str),
    )
    candidate_topics = [
        snapshot
        for snapshot in remaining_status.values()
        if snapshot.slug != "misc" and snapshot.week_mentions > 0
    ]
    candidate_topics.sort(
        key=lambda item: (
            _STATUS_PRIORITY.get(_status_for_prompt(item), 999),
            -item.week_mentions,
            item.slug,
        )
    )
    top_n = min(8, len(candidate_topics))
    top_topics = candidate_topics[:top_n]

    week_entries_after_merge = _collect_topic_week_entries(daily_summaries)
    l5_input = {
        "topics_to_write": [
            {
                "slug": topic.slug,
                "display_name": str(_topic_map(remaining_topics).get(topic.slug, {}).get("display_name") or topic.display_name),
                "status": _status_for_prompt(topic),
                "weekly_entries": week_entries_after_merge.get(topic.slug, []),
                "previous_week_narrative": None,
            }
            for topic in top_topics
        ]
    }

    l6_input = {
        "scope_week": week_str,
        "weekly_dailies": [
            {
                "date": str(daily.get("date") or ""),
                "content_full": "\n".join(
                    str(cluster.get("narrative_one_line") or "") for cluster in (daily.get("clusters") or []) if isinstance(cluster, dict)
                ),
            }
            for daily in daily_summaries
        ],
        "topic_status": [
            {
                "slug": topic.slug,
                "display_name": topic.display_name,
                "status": _status_for_prompt(topic),
                "weekly_count": topic.week_mentions,
                "last_week_count": topic.last_week_mentions,
            }
            for topic in top_topics
        ],
        "hud_inputs_this_week": [],
        "last_week_observation_text": None,
    }

    l5_spec = load_prompt("L5_weekly_main_narrative")
    l6_spec = load_prompt("L6_explorer")
    l5_prompt = _build_prompt(l5_spec.body, "L5_weekly_main_narrative", l5_input)
    l6_prompt = _build_prompt(l6_spec.body, "L6_explorer", l6_input)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_l5 = executor.submit(gateway.call, "L5_weekly_main_narrative", l5_prompt, input_data=l5_input)
            fut_l6 = executor.submit(gateway.call, "L6_explorer", l6_prompt, input_data=l6_input)
            l5_output = fut_l5.result()
            l6_output = fut_l6.result()
    except (LLMCallError, OSError, ValueError, RuntimeError) as exc:
        raise WeeklyOrchestratorError(f"weekly L5/L6 failed: {exc}") from exc

    rendered = _render_weekly_markdown(
        week_str,
        top_topics,
        _topic_map(remaining_topics),
        l5_output if isinstance(l5_output, dict) else {},
        l6_output if isinstance(l6_output, dict) else {},
    )
    weekly_path = _weekly_path(week_str)
    atomic_write_text(weekly_path, rendered)

    _append_log(
        {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "capability": "weekly_orchestrator",
            "week": week_str,
            "daily_count": len(daily_summaries),
            "candidate_pairs": len(raw_pairs),
            "sent_pairs": len(scored_pairs),
            "merged_pairs": merged_pairs,
            "top_topics": [topic.slug for topic in top_topics],
            "weekly_path": str(weekly_path),
        }
    )

    return str(weekly_path)
