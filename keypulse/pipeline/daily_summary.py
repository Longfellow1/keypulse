from __future__ import annotations

import json
import re
import hashlib
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
_OPTIONAL_CLUSTER_KEYS = {"peak_event_density"}
_COST_KEYS = {"in_tokens", "out_tokens", "cost_usd"}
_TOPIC_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_TIME_IN_TEXT_RE = re.compile(r"\b([01]\d|2[0-3]):([0-5]\d)\b")


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
