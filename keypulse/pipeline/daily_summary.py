from __future__ import annotations

import json
import re
import hashlib
import unicodedata
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from keypulse.obsidian.quality_gate import score_daily
from keypulse.pipeline.event_intake import cap_events_by_source
from keypulse.store.repository import query_raw_events
from keypulse.utils.paths import get_data_dir
from keypulse.utils.dates import local_day_bounds, local_timezone

if TYPE_CHECKING:
    from keypulse.pipeline.run_record import RunRecorder


_TIME_TEXT = re.compile(r"^\d{2}:\d{2}$")
_CLUSTER_KEYS = {
    "slug",
    "display_name",
    "narrative_one_line",
    "event_count",
    "time_range",
    "merge_candidate_with",
}
_SCENE_METRIC_KEYS = {"dwell_minutes", "revisit_count", "cross_app_count"}
_OPTIONAL_CLUSTER_KEYS = {"peak_event_density", *_SCENE_METRIC_KEYS}
_EVENT_KEYS = {
    "cluster_id",
    "display_name",
    "narrative_one_line",
    "event_count",
    "time_range",
    "anchored_to",
}
_OPTIONAL_EVENT_KEYS = {"peak_event_density", "merge_candidate_with", *_SCENE_METRIC_KEYS}
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


def _trace_parse_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trace_local_time_label(value: str | None) -> str:
    parsed = _trace_parse_datetime(value)
    if parsed is None:
        return "—"
    return parsed.astimezone(local_timezone()).strftime("%H:%M")


def _trace_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _trace_orchestrator_rows(date_text: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _trace_read_jsonl(get_data_dir() / "log.md")
        if str(row.get("capability") or "").strip() == "daily_orchestrator"
        and str(row.get("date") or "").strip() == date_text
    ]
    return sorted(rows, key=lambda row: _trace_parse_datetime(str(row.get("ts") or "")) or datetime.min.replace(tzinfo=timezone.utc))


def _trace_orchestrator_window(rows: list[dict[str, Any]]) -> tuple[datetime | None, datetime | None]:
    ts_values = [
        parsed
        for parsed in (_trace_parse_datetime(str(row.get("ts") or "")) for row in rows)
        if parsed is not None
    ]
    if not ts_values:
        return None, None
    window_start = min(ts_values)
    window_end = max(ts_values) + timedelta(seconds=60)
    return window_start, window_end


def _trace_cost_rows(*, window_start: datetime | None, window_end: datetime | None) -> list[dict[str, Any]]:
    if window_start is None or window_end is None:
        return []
    rows = []
    for row in _trace_read_jsonl(get_data_dir() / "cost.jsonl"):
        parsed = _trace_parse_datetime(str(row.get("ts") or ""))
        if parsed is None:
            continue
        if parsed < window_start or parsed > window_end:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: _trace_parse_datetime(str(row.get("ts") or "")) or datetime.min.replace(tzinfo=timezone.utc))


def _trace_select_row(rows: list[dict[str, Any]], **criteria: Any) -> dict[str, Any] | None:
    filtered = rows
    for key, expected in criteria.items():
        if expected is None:
            continue
        if key == "tier":
            filtered = [row for row in filtered if str(row.get(key) or "").strip() == str(expected)]
        else:
            filtered = [row for row in filtered if row.get(key) == expected]
    if not filtered:
        return None
    return max(
        filtered,
        key=lambda row: _trace_parse_datetime(str(row.get("ts") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def _trace_quality_gate_label(main_body: str) -> str:
    score = score_daily(main_body)
    if score.total_chars <= 0:
        return "refused"
    if score.template_density > 0.15 or score.unique_word_ratio < 0.4:
        return "refused"
    if score.thing_count < 3:
        return "warn"
    return "ok"


def _trace_event_text(row: dict[str, Any]) -> str:
    for key in ("content_text", "body", "title", "window_title", "app_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return " ".join(value.split())[:80]
    return "—"


def _trace_sample_events(raw_rows: list[dict[str, Any]], *, capped_limit: int | None = None) -> list[dict[str, Any]]:
    if capped_limit is not None and raw_rows:
        sample_rows, _ = cap_events_by_source([dict(row) for row in raw_rows], limit=max(int(capped_limit or 0), 1))
        return sample_rows
    return list(raw_rows)


def _display_width(text: str) -> int:
    width = 0
    for char in str(text or ""):
        # Obsidian/source-mode monospace rendering treats Ambiguous(A) glyphs
        # like EM DASH as single-column in this environment.
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad_display(text: str, width: int, *, align: str = "left") -> str:
    value = str(text or "")
    pad = max(int(width or 0) - _display_width(value), 0)
    if align == "right":
        return (" " * pad) + value
    return value + (" " * pad)


def _render_text_kv_block(rows: list[tuple[str, str]]) -> list[str]:
    if not rows:
        return []
    key_width = max((_display_width(key) for key, _value in rows), default=0) + 2
    lines = ["```text"]
    for key, value in rows:
        lines.append(f"{_pad_display(key, key_width)}: {value}")
    lines.extend(["```", ""])
    return lines


def _render_text_table_block(
    *,
    headers: list[str],
    rows: list[list[str]],
    align_right_cols: set[int] | None = None,
) -> list[str]:
    if not headers:
        return []
    align_right = set(align_right_cols or set())
    widths = []
    for index, header in enumerate(headers):
        column_cells = [str(row[index] if index < len(row) else "") for row in rows]
        widths.append(max([_display_width(header), *(_display_width(cell) for cell in column_cells)]))

    def render_row(values: list[str], is_header: bool = False) -> str:
        cells: list[str] = []
        for index, header in enumerate(headers):
            value = str(values[index] if index < len(values) else "")
            align = "right" if index in align_right and not is_header else "left"
            cells.append(_pad_display(value, widths[index], align=align))
        return "  ".join(cells).rstrip()

    lines = ["```text", render_row(headers, is_header=True)]
    for row in rows:
        lines.append(render_row(row))
    lines.extend(["```", ""])
    return lines


def _trace_source_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        source = str(row.get("source") or "").strip() or "unknown"
        parsed = _trace_parse_datetime(str(row.get("ts_start") or row.get("created_at") or ""))
        bucket = aggregates.setdefault(
            source,
            {
                "source": source,
                "events": 0,
                "earliest": None,
                "latest": None,
            },
        )
        bucket["events"] = int(bucket.get("events") or 0) + 1
        if parsed is None:
            continue
        earliest = bucket.get("earliest")
        latest = bucket.get("latest")
        if earliest is None or parsed < earliest:
            bucket["earliest"] = parsed
        if latest is None or parsed > latest:
            bucket["latest"] = parsed

    def _has_matching_source(expected: str, present_sources: set[str]) -> bool:
        return any(
            source == expected
            or source.startswith(f"{expected}_")
            or expected.startswith(f"{source}_")
            for source in present_sources
        )

    try:
        from keypulse.config import Config

        cfg = Config.load()
        enabled_watchers = {
            str(name)
            for name, enabled in (cfg.watchers.model_dump() if hasattr(cfg.watchers, "model_dump") else {}).items()
            if bool(enabled)
        }
        if bool(getattr(getattr(cfg, "sources", None), "scheduler", None) and getattr(cfg.sources.scheduler, "enabled", False)):
            enabled_watchers.add("markdown_vault")
        if bool(getattr(cfg, "browser_history", None) and getattr(cfg.browser_history, "enabled", False)):
            enabled_watchers.add("browser_history")
    except Exception:
        enabled_watchers = set()

    present_sources = set(aggregates.keys())
    for source in sorted(enabled_watchers):
        if _has_matching_source(source, present_sources):
            continue
        aggregates[source] = {
            "source": source,
            "events": 0,
            "earliest": None,
            "latest": None,
        }

    rows = sorted(
        aggregates.values(),
        key=lambda item: (-int(item.get("events") or 0), str(item.get("source") or "")),
    )
    return rows


def _trace_cluster_strategy_label(
    *,
    orchestrator_rows: list[dict[str, Any]],
    cost_rows: list[dict[str, Any]],
    capped_row: dict[str, Any] | None,
) -> str:
    budget_row = _trace_select_row(orchestrator_rows, tier="budget")
    if budget_row is not None:
        return "budget 分步法（pre-cluster + L1/L2/L3）"
    if capped_row is not None or any(str(row.get("capability") or "").strip() == "daily_flagship" for row in cost_rows):
        return "flagship 一步法（LLM 直接产 things）"
    return "其他"


def _render_algorithm_trace_section(
    *,
    date_text: str,
    main_body: str,
    topic_count: int,
    event_card_count: int,
    clusters_hint: int = 0,
) -> list[str]:
    del clusters_hint
    orchestrator_rows = _trace_orchestrator_rows(date_text)
    if not orchestrator_rows:
        return []
    window_start, window_end = _trace_orchestrator_window(orchestrator_rows)
    cost_rows = _trace_cost_rows(window_start=window_start, window_end=window_end)
    capped_row = _trace_select_row(orchestrator_rows, decision="events_capped")
    repair_row = _trace_select_row(orchestrator_rows, decision="flagship_repair")

    try:
        raw_rows = query_raw_events(since=local_day_bounds(date_text)[0], until=local_day_bounds(date_text)[1], limit=50000)
    except Exception:
        return []

    capped_limit = None
    raw_event_count: int | str = "—"
    capped_display: str | int = "—"
    trace_start = None
    if capped_row is not None:
        trace_start = _trace_parse_datetime(str(capped_row.get("ts") or ""))
        raw_event_count = int(capped_row.get("count") or capped_row.get("event_count") or 0) or "—"
        capped_limit = int(capped_row.get("capped_count") or capped_row.get("capped") or 0) or None
        if capped_limit is not None:
            reason = str(capped_row.get("reason") or "token_guard").strip() or "token_guard"
            capped_display = f"{capped_limit} ({reason})"
    elif orchestrator_rows:
        raw_event_count = "—"

    if trace_start is not None:
        cost_rows = [row for row in cost_rows if (_trace_parse_datetime(str(row.get("ts") or "")) or datetime.min.replace(tzinfo=timezone.utc)) >= trace_start]
        if raw_event_count == "—":
            raw_event_count = len(raw_rows)
    elif raw_event_count == "—":
        raw_event_count = len(raw_rows)

    raw_rows = sorted(
        [dict(row) for row in raw_rows],
        key=lambda row: (
            _trace_parse_datetime(str(row.get("ts_start") or row.get("created_at") or "")) or datetime.min.replace(tzinfo=timezone.utc),
            int(row.get("id") or 0) if str(row.get("id") or "").isdigit() else 0,
        ),
    )
    source_rows = _trace_source_rows(raw_rows)

    cluster_strategy = _trace_cluster_strategy_label(
        orchestrator_rows=orchestrator_rows,
        cost_rows=cost_rows,
        capped_row=capped_row,
    )
    quality_gate = _trace_quality_gate_label(main_body)
    trace_rows = _trace_sample_events(raw_rows, capped_limit=capped_limit)
    sample_lines: list[str] = []
    for index, row in enumerate(trace_rows[:5], start=1):
        ts_label = _trace_local_time_label(str(row.get("ts_start") or row.get("created_at") or ""))
        source = str(row.get("source") or "").strip() or "—"
        text = _trace_event_text(row).replace('"', "'")
        sample_lines.append(f"{index}. {ts_label} {source} · \"{text}\"")
    if not sample_lines:
        sample_lines = ["—"]

    lines: list[str] = [
        "",
        "---",
        "",
        "## 🔬 算法 Trace（自检用）",
        "",
        "**数据**",
    ]
    lines.extend(
        _render_text_kv_block(
            [
                ("raw events", str(raw_event_count)),
                ("capped", str(capped_display)),
                ("时间窗", f"{date_text} 00:00 ~ 23:59 {local_timezone()}"),
                ("聚类策略", cluster_strategy),
                ("things", str(topic_count)),
                ("events 卡片", str(event_card_count)),
                ("quality_gate", quality_gate),
            ]
        )
    )

    if source_rows:
        source_table: list[list[str]] = []
        for item in source_rows:
            events_count = int(item.get("events") or 0)
            earliest = item.get("earliest")
            latest = item.get("latest")
            earliest_label = earliest.astimezone(local_timezone()).strftime("%H:%M") if isinstance(earliest, datetime) else "—"
            latest_label = latest.astimezone(local_timezone()).strftime("%H:%M") if isinstance(latest, datetime) else "—"
            status = "ok" if events_count >= 1 else "⚠ silent"
            source_table.append([str(item.get("source") or ""), str(events_count), earliest_label, latest_label, status])
        lines.append("**数据采集源**（当日 raw events 按 source 聚合）")
        lines.extend(
            _render_text_table_block(
                headers=["source", "events", "最早", "最晚", "状态"],
                rows=source_table,
                align_right_cols={1},
            )
        )

    if repair_row is not None:
        before_things = int(repair_row.get("before_things") or 0)
        repair_raw = repair_row.get("repair_things")
        repair_things = "—" if repair_raw is None else str(int(repair_raw or 0))
        final_things = int(repair_row.get("final_things") or 0)
        trigger_reason = str(repair_row.get("reason") or "things_lt_3").strip() or "things_lt_3"
        failure_reason = str(repair_row.get("failure_reason") or "").strip() or "—"
        lines.extend(
            [
                "**repair 自检**",
                f"- 触发原因：{trigger_reason}",
                f"- H3 计数：{before_things} → {repair_things} → {final_things}",
                f"- 失败原因：{failure_reason}",
                "",
            ]
        )

    if cost_rows:
        table_rows: list[list[str]] = []
        for row in cost_rows:
            stage = str(row.get("capability") or "").strip() or "—"
            model = str(row.get("model") or "").strip() or "—"
            model = model.split("/")[-1] if "/" in model else model
            in_tokens = int(row.get("in_tokens") or 0)
            out_tokens = int(row.get("out_tokens") or 0)
            if bool(row.get("cache_hit")):
                status = "cache"
            elif out_tokens <= 0:
                status = "failed: empty content"
            else:
                status = "ok"
            table_rows.append([stage, model, f"{in_tokens}→{out_tokens}", status])
        lines.append("**LLM 调用**")
        lines.extend(
            _render_text_table_block(
                headers=["stage", "model", "in→out tokens", "状态"],
                rows=table_rows,
            )
        )

    lines.extend(
        [
            "**进 LLM 的 events sample**（capped 后头 5 条）",
        ]
    )
    for line in sample_lines:
        lines.append(line)
    lines.append("")
    return lines


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
    if "dwell_minutes" in cluster:
        result["dwell_minutes"] = float(cluster["dwell_minutes"] or 0.0)
    if "revisit_count" in cluster:
        result["revisit_count"] = int(cluster["revisit_count"] or 0)
    if "cross_app_count" in cluster:
        result["cross_app_count"] = int(cluster["cross_app_count"] or 0)
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
    if "dwell_minutes" in event:
        result["dwell_minutes"] = float(event["dwell_minutes"] or 0.0)
    if "revisit_count" in event:
        result["revisit_count"] = int(event["revisit_count"] or 0)
    if "cross_app_count" in event:
        result["cross_app_count"] = int(event["cross_app_count"] or 0)
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
    for key in _SCENE_METRIC_KEYS:
        if key in cluster:
            output[key] = int(cluster.get(key) or 0) if key.endswith("_count") else float(cluster.get(key) or 0.0)
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
    for key in _SCENE_METRIC_KEYS:
        if key in event:
            output[key] = int(event.get(key) or 0) if key.endswith("_count") else float(event.get(key) or 0.0)
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

    narrative_md_raw = payload.get("narrative_markdown", "")
    if narrative_md_raw is None:
        narrative_md = ""
    elif isinstance(narrative_md_raw, str):
        narrative_md = narrative_md_raw
    else:
        raise ValueError("narrative_markdown must be string")

    normalized = {
        "date": date_text,
        "topics": topics,
        "events": events,
        "unanchored": unanchored,
        "clusters": [_event_to_legacy_cluster(event) for event in events],
        "misc_event_ids": misc_event_ids,
        "topic_status_snapshot": topic_status_snapshot,
        "cost": _validate_cost(payload["cost"]),
        "narrative_markdown": narrative_md,
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
    narrative_markdown: str | None = None,
    recorder: "RunRecorder | None" = None,
    stage: str = "persist_daily_summary",
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
        "narrative_markdown": str(narrative_markdown) if narrative_markdown else "",
    }
    normalized = _validate_summary_payload(payload)

    target = _summary_dir() / f"{normalized['date']}.json"
    rendered = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if recorder is not None:
        from keypulse.pipeline.artifact_writer import write_artifact

        write_artifact(recorder, target, rendered, stage=stage)
        return target
    tmp_path = target.with_name(f"{target.name}.tmp")
    try:
        tmp_path.write_text(rendered, encoding="utf-8")
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


_EVENT_CARD_NOISE_PREFIXES = (
    "export-",
    "https-",
    "http-",
    "redacted-shell",
    "uncategorized",
    "obsidian-clipboard-copy",
    "https-github-com-",
    "https-mp-weixin-",
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_START_RE = re.compile(r"^[0-9A-Za-z]")


def _event_card_score(slug: str, title: str) -> int:
    slug_text = str(slug or "").strip().lower()
    title_text = " ".join(str(title or "").split()).strip()
    title_lower = title_text.lower()
    if (
        any(slug_text.startswith(prefix) or title_lower.startswith(prefix) for prefix in _EVENT_CARD_NOISE_PREFIXES)
        or "users-harland-" in slug_text
        or "/users/harland/" in title_lower
        or " users/harland/" in title_lower
    ):
        return 0

    score = 1
    if _CJK_RE.search(title_text):
        score += 3
    if 8 <= len(title_text) <= 40:
        score += 2
    if title_text and _ASCII_START_RE.match(title_text) is None:
        score += 1
    return score


def filter_daily_event_cards(
    cards: list[tuple[str, str]],
    *,
    model_gateway: Any | None = None,
    target_count: int = 6,
) -> list[tuple[str, str]]:
    if not cards:
        return []

    del model_gateway
    limit = min(max(int(target_count or 6), 5), 8, len(cards))
    ranked = sorted(
        enumerate(cards),
        key=lambda item: (-_event_card_score(item[1][0], item[1][1]), item[0]),
    )
    selected_indices = sorted(index for index, _card in ranked[:limit])
    return [cards[index] for index in selected_indices]


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
    previous_plan: str = "",
    tomorrow_plan: str = "",
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

    topic_list = [topic for topic in (topics or []) if isinstance(topic, dict)]
    event_list = [event for event in (events or []) if isinstance(event, dict)]

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

    def _extract_h3_section(source: str, candidates: list[str]) -> str:
        if not source.strip():
            return ""
        targets = [c.strip() for c in candidates if c and c.strip()]
        if not targets:
            return ""
        lines = source.splitlines()
        in_section = False
        collected: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                if in_section:
                    break
                continue
            if stripped.startswith("### "):
                if in_section:
                    break
                if any(t in stripped for t in targets):
                    in_section = True
                continue
            if in_section:
                collected.append(line)
        return "\n".join(collected).strip()

    source_markdown = str(narrative_markdown or "")
    highlight_section = _extract_section(source_markdown, "今日要点")

    lines = ["📍 Asia/Shanghai", "", f"# {date_text}"]
    previous_plan_text = " ".join(str(previous_plan or "").split()).strip()
    if previous_plan_text:
        lines.extend(["", f"> 💭 昨天你说想：{previous_plan_text}"])
    lines.extend(["", "## 今日要点", ""])
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
        h3_candidates = [display]
        for ref in refs:
            cluster_display = str((event_lookup.get(ref) or {}).get("display_name") or "").strip()
            if cluster_display:
                h3_candidates.append(cluster_display)
        narrative = _extract_h3_section(source_markdown, h3_candidates)
        if not narrative:
            narrative = str(topic.get("narrative") or "").strip()
        if not narrative and refs:
            narrative = "；".join(
                str((event_lookup.get(ref) or {}).get("narrative_one_line") or "").strip()
                for ref in refs
                if str((event_lookup.get(ref) or {}).get("narrative_one_line") or "").strip()
            )
        lines.append(narrative or "—")
        lines.append("")
    if not topic_list:
        lines.extend(["—", ""])

    # TODO(M4): 整段删除（legacy events/ 事件卡区块下线）
    # NOTE: 这里不再渲染“今天的事件卡”，避免继续依赖 ~/.keypulse/events 目录。
    selected_event_cards: list[tuple[str, str]] = []

    cross_day_section = _extract_section(source_markdown, "跨日延续").strip()
    if cross_day_section:
        lines.extend(["", "## 跨日延续", "", cross_day_section])

    stuck_section = _extract_section(source_markdown, "今天的卡壳").strip()
    if stuck_section:
        lines.extend(["", "## 今天的卡壳", "", stuck_section])

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

    tomorrow_plan_text = " ".join(str(tomorrow_plan or "").split()).strip() or "______"
    lines.extend(["", "## 明日的锚点", "", f"> 明天我想：{tomorrow_plan_text}", ">", "> _写一句话留给明天的自己_"])

    if event_list and not topic_list:
        lines.extend(["", "<!-- events_count: {} -->".format(len(event_list))])
    main_body = "\n".join(lines).strip()
    trace_lines = _render_algorithm_trace_section(
        date_text=date_text,
        main_body=main_body,
        topic_count=len(topic_list),
        event_card_count=len(selected_event_cards),
        clusters_hint=len(clusters or []),
    )
    if trace_lines:
        lines.extend(trace_lines)
    lines.append("")
    return "\n".join(lines).strip()
