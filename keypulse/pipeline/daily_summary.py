from __future__ import annotations

import json
import re
from datetime import date as date_cls
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
_COST_KEYS = {"in_tokens", "out_tokens", "cost_usd"}


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
    extra = sorted(keys - _CLUSTER_KEYS)
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

    return {
        "slug": slug,
        "display_name": display_name,
        "narrative_one_line": narrative_one_line,
        "event_count": event_count,
        "time_range": [start, end],
        "merge_candidate_with": merge_candidate_with,
    }


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

    required = {"date", "clusters", "misc_event_ids", "topic_status_snapshot", "cost"}
    keys = set(payload.keys())
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing:
        raise ValueError(f"daily summary missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"daily summary unexpected fields: {', '.join(extra)}")

    date_text = _validate_date(str(payload["date"]))

    clusters_raw = payload["clusters"]
    if not isinstance(clusters_raw, list):
        raise ValueError("clusters must be array")
    clusters = [_validate_cluster(cluster, index) for index, cluster in enumerate(clusters_raw)]

    misc_raw = payload["misc_event_ids"]
    if not isinstance(misc_raw, list):
        raise ValueError("misc_event_ids must be array")
    misc_event_ids = [str(item) for item in misc_raw]

    topic_status_snapshot = payload["topic_status_snapshot"]
    if not isinstance(topic_status_snapshot, dict):
        raise ValueError("topic_status_snapshot must be object")

    normalized = {
        "date": date_text,
        "clusters": clusters,
        "misc_event_ids": misc_event_ids,
        "topic_status_snapshot": topic_status_snapshot,
        "cost": _validate_cost(payload["cost"]),
    }
    return normalized


def write_daily_summary(date: str, clusters, misc, topic_snapshot, cost) -> Path:
    payload = {
        "date": date,
        "clusters": clusters,
        "misc_event_ids": misc,
        "topic_status_snapshot": topic_snapshot,
        "cost": cost,
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
