from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from math import inf
from typing import Any, Iterable


@dataclass(frozen=True)
class TopicStatusSnapshot:
    slug: str
    display_name: str
    status: str
    lifecycle_status: str
    week_mentions: int
    last_week_mentions: int
    prev_week_mentions: int
    acceleration: float
    acceleration_delta: int
    acceleration_ratio: float
    first_seen: str | None
    last_seen: str | None


def _parse_date(value: Any) -> date_cls | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        return None


def _week_bounds(week_str: str) -> tuple[date_cls, date_cls]:
    text = str(week_str or "").strip()
    if "-W" not in text:
        raise ValueError(f"invalid ISO week: {week_str}")
    year_text, week_text = text.split("-W", 1)
    try:
        year = int(year_text)
        week = int(week_text)
        start = date_cls.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: {week_str}") from exc
    return start, start + timedelta(days=6)


def _topic_keyed(topics_index: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in topics_index:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        result[slug] = dict(row)
    return result


def _week_mentions_from_daily(daily_summaries: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in daily_summaries:
        if not isinstance(summary, dict):
            continue
        clusters = summary.get("clusters") or []
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug or slug == "misc":
                continue
            event_count = cluster.get("event_count")
            if isinstance(event_count, bool):
                continue
            if isinstance(event_count, int):
                value = event_count
            else:
                try:
                    value = int(str(event_count))
                except (TypeError, ValueError):
                    value = 1
            if value <= 0:
                continue
            counts[slug] = counts.get(slug, 0) + value
    return counts


def _baseline_mean(last_week_mentions: int, prev_week_mentions: int) -> float:
    return (float(last_week_mentions) + float(prev_week_mentions)) / 2.0


def _is_ongoing(
    week_mentions: int,
    history: list[int],
    topic_meta: dict[str, Any],
) -> bool:
    if week_mentions <= 0:
        return False
    if bool(topic_meta.get("pinned")):
        return True

    # M1 best-effort: infer consecutive streak from available weekly history.
    recent = list(history) + [week_mentions]
    streak = 0
    for value in reversed(recent):
        if int(value) > 0:
            streak += 1
        else:
            break
    return streak >= 4


def _pick_status(
    *,
    week_mentions: int,
    last_week_mentions: int,
    prev_week_mentions: int,
    acceleration: int,
    first_seen: date_cls | None,
    week_start: date_cls,
    last_seen_before_week: date_cls | None,
    history: list[int],
    topic_meta: dict[str, Any],
) -> str:
    if week_mentions <= 0:
        return "steady"

    if first_seen is not None and week_start <= first_seen <= week_start + timedelta(days=6):
        return "new"

    if _is_ongoing(week_mentions, history, topic_meta):
        return "ongoing"

    if last_seen_before_week is not None and (week_start - last_seen_before_week).days > 14:
        return "revived"

    delta_this = week_mentions - last_week_mentions
    if abs(delta_this) <= 1:
        return "steady"

    baseline = _baseline_mean(last_week_mentions, prev_week_mentions)
    if acceleration > 0 and week_mentions >= 2:
        return "accelerating"
    if delta_this < 0 and week_mentions < last_week_mentions and (baseline <= 0 or week_mentions < (2.0 * baseline / 3.0)):
        return "declining"
    return "steady"


def compute_topic_status(
    *,
    topics_index: list[dict[str, Any]],
    daily_summaries: list[dict[str, Any]],
    week_str: str,
    weekly_history: dict[str, list[int]] | None = None,
) -> dict[str, TopicStatusSnapshot]:
    week_start, _week_end = _week_bounds(week_str)
    weekly_history = dict(weekly_history or {})

    topics_by_slug = _topic_keyed(topics_index)
    week_mentions_map = _week_mentions_from_daily(daily_summaries)
    all_slugs = sorted(set(topics_by_slug.keys()) | set(week_mentions_map.keys()))

    result: dict[str, TopicStatusSnapshot] = {}
    for slug in all_slugs:
        meta = dict(topics_by_slug.get(slug) or {})
        display_name = str(meta.get("display_name") or slug).strip() or slug
        first_seen_raw = _parse_date(meta.get("first_seen"))
        last_seen_raw = _parse_date(meta.get("last_seen"))

        history = [int(item) for item in (weekly_history.get(slug) or [])]
        last_week_mentions = history[-1] if len(history) >= 1 else 0
        prev_week_mentions = history[-2] if len(history) >= 2 else 0

        week_mentions = int(week_mentions_map.get(slug, 0))
        delta_this = week_mentions - last_week_mentions
        delta_last = last_week_mentions - prev_week_mentions
        acceleration = delta_this - delta_last

        baseline = _baseline_mean(last_week_mentions, prev_week_mentions)
        if baseline <= 0:
            ratio = inf if week_mentions > 0 else 0.0
        else:
            ratio = week_mentions / baseline

        lifecycle = "active"
        if week_mentions <= 0:
            lifecycle = "dormant"
        elif first_seen_raw is not None and week_start <= first_seen_raw <= week_start + timedelta(days=6):
            lifecycle = "emerging"

        status = _pick_status(
            week_mentions=week_mentions,
            last_week_mentions=last_week_mentions,
            prev_week_mentions=prev_week_mentions,
            acceleration=acceleration,
            first_seen=first_seen_raw,
            week_start=week_start,
            last_seen_before_week=last_seen_raw,
            history=history,
            topic_meta=meta,
        )

        result[slug] = TopicStatusSnapshot(
            slug=slug,
            display_name=display_name,
            status=status,
            lifecycle_status=lifecycle,
            week_mentions=week_mentions,
            last_week_mentions=last_week_mentions,
            prev_week_mentions=prev_week_mentions,
            acceleration=float(ratio),
            acceleration_delta=acceleration,
            acceleration_ratio=float(ratio),
            first_seen=first_seen_raw.isoformat() if first_seen_raw else None,
            last_seen=last_seen_raw.isoformat() if last_seen_raw else None,
        )

    return result
