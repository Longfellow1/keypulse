#!/usr/bin/env python3
"""Entity extractor LLM-as-judge multi-day eval harness.

Usage:
  python -m scripts.eval_entity_extractor
  python scripts/eval_entity_extractor.py
  python -m scripts.eval_entity_extractor --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from keypulse.config import Config
from keypulse.pipeline.daily_orchestrator import _flagship_event_score, cap_events_by_source
from keypulse.pipeline.daily_strategy import build_prompt, extract_work_unit
from keypulse.pipeline.entity_extractor_llm import extract_for_date
from keypulse.pipeline.model import ModelGateway, load_model_gateway
from keypulse.prompts.loader import load_prompt
from keypulse.store.db import init_db
from keypulse.store.repository import query_raw_events
from keypulse.utils.dates import local_day_bounds


TARGET_DATES: list[str] = [
    "2026-05-19",
    "2026-05-07",
    "2026-05-13",
    "2026-05-21",
    "2026-05-06",
    "2026-05-11",
    "2026-05-20",
]
DEFAULT_JSON_OUT = Path("docs/entity-extractor-eval-2026-05.json")
DEFAULT_MD_OUT = Path("docs/entity-extractor-eval-2026-05.md")
EVAL_EVENT_CAP = 60
FAIL_THRESHOLD = 0.7

_EVENT_ID_FROM_WARNING_RE = re.compile(r"\\bevent\\s+([A-Za-z0-9_-]+)", re.IGNORECASE)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multi-day entity_extractor LLM-as-judge evaluation")
    parser.add_argument(
        "--dates",
        nargs="*",
        default=TARGET_DATES,
        help="Target dates in YYYY-MM-DD; defaults to fixed 7-day sample",
    )
    parser.add_argument(
        "--json-out",
        default=str(DEFAULT_JSON_OUT),
        help="Output JSON path",
    )
    parser.add_argument(
        "--md-out",
        default=str(DEFAULT_MD_OUT),
        help="Output markdown report path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run with one-day stub data only (no DB/LLM calls)",
    )
    parser.add_argument(
        "--dry-run-date",
        default=TARGET_DATES[0],
        help="Date used for dry-run stub payload",
    )
    return parser.parse_args()


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_raw_event(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _parse_metadata(row.get("metadata_json"))
    entities_raw = metadata.get("entities")
    entities = dict(entities_raw) if isinstance(entities_raw, dict) else {}

    event_id = str(row.get("id") or "").strip()
    if not event_id:
        raise ValueError("raw event row missing id")

    ts_start = str(row.get("ts_start") or "").strip()
    if not ts_start:
        raise ValueError(f"raw event {event_id} missing ts_start")

    app_name = str(row.get("app_name") or metadata.get("app_name") or "unknown").strip() or "unknown"
    payload = dict(row)
    payload.update(
        {
            "id": event_id,
            "ts_start": ts_start,
            "source": str(row.get("source") or "").strip(),
            "event_type": str(row.get("event_type") or "").strip(),
            "speaker": str(row.get("speaker") or "").strip(),
            "app_name": app_name,
            "window_title": str(row.get("window_title") or metadata.get("window_title") or "").strip(),
            "process_name": str(row.get("process_name") or "").strip(),
            "content_text": str(row.get("content_text") or "").strip(),
            "ts_end": row.get("ts_end"),
            "content_hash": row.get("content_hash"),
            "session_id": str(row.get("session_id") or entities.get("session_id") or "").strip(),
            "semantic_weight": row.get("semantic_weight"),
            "user_present": row.get("user_present"),
            "metadata_json": json.dumps({**metadata, "entities": entities}, ensure_ascii=False),
        }
    )
    return payload


def _load_capped_events_for_date(date: str) -> tuple[list[dict[str, Any]], int, bool]:
    since, until = local_day_bounds(date)
    rows = query_raw_events(since=since, until=until, limit=50000)
    rows = sorted(rows, key=lambda item: (str(item.get("ts_start") or ""), int(item.get("id") or 0)))

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            event = _normalize_raw_event(row)
        except ValueError:
            continue
        event["_flagship_score"] = _flagship_event_score(event)
        events.append(event)

    capped_events, was_capped = cap_events_by_source(events, limit=EVAL_EVENT_CAP)
    return capped_events, len(events), was_capped


def _event_for_judge(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _parse_metadata(event.get("metadata_json"))
    entities = metadata.get("entities") if isinstance(metadata.get("entities"), dict) else {}
    file_paths = _string_list((entities or {}).get("file_paths"))
    urls = _string_list((entities or {}).get("urls"))

    return {
        "event_id": str(event.get("id") or "").strip(),
        "ts_start": str(event.get("ts_start") or "").strip(),
        "source": str(event.get("source") or "").strip(),
        "speaker": str(event.get("speaker") or "").strip(),
        "app_name": str(event.get("app_name") or "").strip(),
        "window_title": str(event.get("window_title") or "").strip(),
        "content_text": str(event.get("content_text") or "").strip(),
        "work_unit": extract_work_unit(event),
        "file_paths": file_paths,
        "urls": urls,
    }


def _judge_day(
    *,
    date: str,
    extractor_output: dict[str, Any],
    capped_events: list[dict[str, Any]],
    gateway: ModelGateway,
) -> dict[str, Any]:
    spec = load_prompt("L9_entity_extractor_judge")
    input_data = {
        "date": date,
        "extractor_output": extractor_output,
        "events": [_event_for_judge(event) for event in capped_events],
    }
    prompt = build_prompt(spec.body, "L9_entity_extractor_judge", input_data)
    raw_response: Any = gateway.call(
        "L9_entity_extractor_judge",
        prompt,
        input_data=input_data,
    )
    if not isinstance(raw_response, dict):
        raise ValueError(f"L9_entity_extractor_judge returned non-dict for {date}: {type(raw_response)}")
    return raw_response


def _score(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0:
        return 0.0
    if numeric > 1:
        return 1.0
    return numeric


def _avg(values: Iterable[float]) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    return sum(seq) / len(seq)


def _norm_event_id(value: Any) -> str:
    return str(value or "").strip()


def _warning_event_ids(warnings: list[Any]) -> set[str]:
    out: set[str] = set()
    for warning in warnings:
        text = str(warning or "")
        if not text.strip():
            continue
        for match in _EVENT_ID_FROM_WARNING_RE.findall(text):
            token = str(match).strip()
            if token:
                out.add(token)
    return out


def _extractor_reported_ids(extractor_output: dict[str, Any]) -> set[str]:
    reported: set[str] = set()
    mapping = extractor_output.get("event_entity_map")
    if isinstance(mapping, list):
        for item in mapping:
            if not isinstance(item, Mapping):
                continue
            if bool(item.get("needs_review", False)):
                event_id = _norm_event_id(item.get("event_id"))
                if event_id:
                    reported.add(event_id)

    warnings_raw = extractor_output.get("cross_entity_warning")
    if isinstance(warnings_raw, list):
        reported.update(_warning_event_ids(warnings_raw))
    return reported


def _entity_display(entity: Mapping[str, Any]) -> str:
    return str(entity.get("name") or "").strip()


def _day_avg_purity(day: dict[str, Any]) -> float:
    per_entity = day.get("judge_output", {}).get("per_entity")
    if not isinstance(per_entity, list):
        return 0.0
    return _avg(_score(item.get("entity_purity", {}).get("score")) for item in per_entity if isinstance(item, Mapping))


def _day_avg_completeness(day: dict[str, Any]) -> float:
    per_entity = day.get("judge_output", {}).get("per_entity")
    if not isinstance(per_entity, list):
        return 0.0
    return _avg(_score(item.get("entity_completeness", {}).get("score")) for item in per_entity if isinstance(item, Mapping))


def _day_overall_quality(day: dict[str, Any]) -> float:
    return _score(day.get("judge_output", {}).get("per_day", {}).get("overall_quality", {}).get("score"))


def _aggregate_per_entity(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}

    for day in days:
        date = str(day.get("date") or "")
        per_entity = day.get("judge_output", {}).get("per_entity")
        if not isinstance(per_entity, list):
            continue

        for item in per_entity:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("entity_name") or "").strip()
            if not name:
                continue
            node = bucket.setdefault(name, {"entity_name": name, "purity_scores": [], "completeness_scores": [], "days": set()})
            node["purity_scores"].append(_score(item.get("entity_purity", {}).get("score")))
            node["completeness_scores"].append(_score(item.get("entity_completeness", {}).get("score")))
            node["days"].add(date)

    out: list[dict[str, Any]] = []
    for name in sorted(bucket):
        node = bucket[name]
        out.append(
            {
                "entity_name": name,
                "days": len(node["days"]),
                "avg_entity_purity": _avg(node["purity_scores"]),
                "avg_entity_completeness": _avg(node["completeness_scores"]),
            }
        )
    return out


def _aggregate_failures(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for day in days:
        date = str(day.get("date") or "")
        per_entity = day.get("judge_output", {}).get("per_entity")
        if not isinstance(per_entity, list):
            continue

        for item in per_entity:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("entity_name") or "").strip()
            purity = _score(item.get("entity_purity", {}).get("score"))
            completeness = _score(item.get("entity_completeness", {}).get("score"))
            if purity >= FAIL_THRESHOLD and completeness >= FAIL_THRESHOLD:
                continue

            wrong_ids = [
                _norm_event_id(value)
                for value in (item.get("entity_purity", {}).get("wrong_event_ids") or [])
                if _norm_event_id(value)
            ]
            missing_ids = [
                _norm_event_id(value)
                for value in (item.get("entity_completeness", {}).get("missing_event_ids") or [])
                if _norm_event_id(value)
            ]
            failures.append(
                {
                    "date": date,
                    "entity_name": name,
                    "entity_purity": purity,
                    "entity_completeness": completeness,
                    "wrong_event_ids": wrong_ids,
                    "missing_event_ids": missing_ids,
                    "purity_reasoning": str(item.get("entity_purity", {}).get("reasoning") or "").strip(),
                    "completeness_reasoning": str(item.get("entity_completeness", {}).get("reasoning") or "").strip(),
                }
            )

    failures.sort(key=lambda item: (item["date"], item["entity_name"]))
    return failures


def _aggregate_cross_entity_warning(days: list[dict[str, Any]]) -> dict[str, Any]:
    reported_pairs: set[tuple[str, str]] = set()
    true_pairs: set[tuple[str, str]] = set()
    overlap_pairs: set[tuple[str, str]] = set()

    per_day_details: list[dict[str, Any]] = []

    for day in days:
        date = str(day.get("date") or "")
        extractor_output = day.get("extractor_output") or {}
        reported_ids = _extractor_reported_ids(extractor_output if isinstance(extractor_output, dict) else {})

        precision = day.get("judge_output", {}).get("per_day", {}).get("cross_entity_warning_precision", {})
        recall = day.get("judge_output", {}).get("per_day", {}).get("cross_entity_warning_recall", {})

        tp_ids = {
            _norm_event_id(value)
            for value in (precision.get("true_positive_event_ids") or [])
            if _norm_event_id(value)
        }
        fp_ids = {
            _norm_event_id(value)
            for value in (precision.get("false_positive_event_ids") or [])
            if _norm_event_id(value)
        }
        missed_ids = {
            _norm_event_id(value)
            for value in (recall.get("missed_event_ids") or [])
            if _norm_event_id(value)
        }

        true_ids = tp_ids | missed_ids

        for event_id in reported_ids:
            reported_pairs.add((date, event_id))
        for event_id in true_ids:
            true_pairs.add((date, event_id))
        for event_id in tp_ids:
            overlap_pairs.add((date, event_id))

        per_day_details.append(
            {
                "date": date,
                "extractor_reported_count": len(reported_ids),
                "judge_true_cross_count": len(true_ids),
                "overlap_count": len(tp_ids),
                "precision": _score(precision.get("score")),
                "recall": _score(recall.get("score")),
                "false_positive_event_ids": sorted(fp_ids),
                "missed_event_ids": sorted(missed_ids),
            }
        )

    reported_total = len(reported_pairs)
    true_total = len(true_pairs)
    overlap_total = len(overlap_pairs)
    union_total = len(reported_pairs | true_pairs)

    return {
        "extractor_reported_total": reported_total,
        "judge_true_cross_total": true_total,
        "overlap_total": overlap_total,
        "overlap_rate": (overlap_total / union_total) if union_total else 1.0,
        "precision": (overlap_total / reported_total) if reported_total else (1.0 if true_total == 0 else 0.0),
        "recall": (overlap_total / true_total) if true_total else 1.0,
        "per_day": per_day_details,
    }


def _spotlight_2026_05_19(days: list[dict[str, Any]]) -> dict[str, Any]:
    day = next((item for item in days if str(item.get("date")) == "2026-05-19"), None)
    if day is None:
        return {"date": "2026-05-19", "available": False}

    extractor = day.get("extractor_output") if isinstance(day.get("extractor_output"), dict) else {}
    event_map = extractor.get("event_entity_map") if isinstance(extractor, dict) else []
    events = day.get("capped_events") if isinstance(day.get("capped_events"), list) else []

    entities = extractor.get("entities") if isinstance(extractor, dict) else []
    entity_names = [
        _entity_display(item)
        for item in entities
        if isinstance(item, Mapping) and _entity_display(item)
    ]

    mapping_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(event_map, list):
        for row in event_map:
            if not isinstance(row, Mapping):
                continue
            event_id = _norm_event_id(row.get("event_id"))
            if event_id:
                mapping_by_id[event_id] = {
                    "event_id": event_id,
                    "primary_entity": str(row.get("primary_entity") or "").strip(),
                    "secondary_entities": [
                        str(value).strip()
                        for value in (row.get("secondary_entities") or [])
                        if str(value).strip()
                    ],
                    "confidence": _score(row.get("confidence")),
                    "needs_review": bool(row.get("needs_review", False)),
                }

    key_event_ids: list[str] = []
    for event_id in ("96881", "97799"):
        if event_id in mapping_by_id:
            key_event_ids.append(event_id)

    for row in sorted(mapping_by_id.values(), key=lambda value: value["event_id"]):
        event_id = row["event_id"]
        if event_id in key_event_ids:
            continue
        key_event_ids.append(event_id)
        if len(key_event_ids) >= 10:
            break

    key_mappings = [mapping_by_id[event_id] for event_id in key_event_ids if event_id in mapping_by_id]

    news_ids: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_id = _norm_event_id(event.get("event_id"))
        if not event_id:
            continue
        haystack = " ".join(
            [
                str(event.get("content_text") or ""),
                str(event.get("window_title") or ""),
                str(event.get("work_unit") or ""),
            ]
        ).lower()
        if "news signal hud" in haystack:
            news_ids.add(event_id)

    news_mappings = [mapping_by_id[event_id] for event_id in sorted(news_ids) if event_id in mapping_by_id]
    news_to_qiqubao = any("奇趣宝" in item.get("primary_entity", "") for item in news_mappings)
    has_qiqubao_primary = any("奇趣宝" in item.get("primary_entity", "") for item in mapping_by_id.values())
    separated = bool(news_mappings) and has_qiqubao_primary and not news_to_qiqubao

    return {
        "date": "2026-05-19",
        "available": True,
        "entities": entity_names,
        "key_mappings": key_mappings,
        "news_signal_hud_event_ids": sorted(news_ids),
        "is_news_signal_hud_separated_from_qiqubao": separated,
    }


def _build_aggregate(days: list[dict[str, Any]]) -> dict[str, Any]:
    per_day_rows: list[dict[str, Any]] = []
    for day in days:
        per_day_rows.append(
            {
                "date": str(day.get("date") or ""),
                "avg_entity_purity": _day_avg_purity(day),
                "avg_entity_completeness": _day_avg_completeness(day),
                "overall_quality": _day_overall_quality(day),
            }
        )

    return {
        "per_day": per_day_rows,
        "per_entity": _aggregate_per_entity(days),
        "cross_entity_warning": _aggregate_cross_entity_warning(days),
        "failure_cases": _aggregate_failures(days),
        "spotlight_2026_05_19": _spotlight_2026_05_19(days),
    }


def _format_float(value: float) -> str:
    return f"{value:.3f}"


def _render_markdown(payload: dict[str, Any]) -> str:
    dates = payload.get("run_dates") or []
    days = payload.get("days") or []
    aggregate = payload.get("aggregate") or {}

    lines: list[str] = []
    lines.append("# Entity Extractor Eval — 2026-05")
    lines.append("")
    lines.append(f"评测样本：{len(dates)} 天")
    lines.append(f"日期：{', '.join(str(item) for item in dates)}")
    lines.append("")

    lines.append("## 7 天总览")
    lines.append("")
    lines.append("| Date | entity_purity | entity_completeness | overall_quality |")
    lines.append("|---|---:|---:|---:|")
    per_day = aggregate.get("per_day") if isinstance(aggregate, dict) else []
    if isinstance(per_day, list):
        for row in per_day:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                "| {date} | {purity} | {completeness} | {overall} |".format(
                    date=str(row.get("date") or ""),
                    purity=_format_float(_score(row.get("avg_entity_purity"))),
                    completeness=_format_float(_score(row.get("avg_entity_completeness"))),
                    overall=_format_float(_score(row.get("overall_quality"))),
                )
            )
    lines.append("")

    lines.append("## 2026-05-19 Spotlight")
    lines.append("")
    spotlight = aggregate.get("spotlight_2026_05_19") if isinstance(aggregate, dict) else {}
    if isinstance(spotlight, Mapping) and spotlight.get("available"):
        entities = spotlight.get("entities") if isinstance(spotlight.get("entities"), list) else []
        lines.append(f"- Entities: {', '.join(str(item) for item in entities) if entities else '无'}")
        separated = bool(spotlight.get("is_news_signal_hud_separated_from_qiqubao", False))
        lines.append(
            "- News Signal HUD 与 奇趣宝是否分开："
            + ("是" if separated else "否")
        )
        news_ids = spotlight.get("news_signal_hud_event_ids") if isinstance(spotlight.get("news_signal_hud_event_ids"), list) else []
        lines.append(f"- News Signal HUD 相关 event_ids: {', '.join(str(item) for item in news_ids) if news_ids else '无'}")
        lines.append("")
        lines.append("关键 event mapping:")
        lines.append("")
        lines.append("| event_id | primary_entity | secondary_entities | confidence | needs_review |")
        lines.append("|---|---|---|---:|---:|")
        for row in spotlight.get("key_mappings") or []:
            if not isinstance(row, Mapping):
                continue
            secondaries = row.get("secondary_entities") if isinstance(row.get("secondary_entities"), list) else []
            lines.append(
                "| {event_id} | {primary} | {secondaries} | {confidence} | {needs_review} |".format(
                    event_id=str(row.get("event_id") or ""),
                    primary=str(row.get("primary_entity") or ""),
                    secondaries=", ".join(str(item) for item in secondaries),
                    confidence=_format_float(_score(row.get("confidence"))),
                    needs_review="true" if bool(row.get("needs_review", False)) else "false",
                )
            )
    else:
        lines.append("- 该日期结果不可用")
    lines.append("")

    lines.append("## Cross-Entity Warning 全局统计")
    lines.append("")
    cross = aggregate.get("cross_entity_warning") if isinstance(aggregate, dict) else {}
    if isinstance(cross, Mapping):
        lines.append(f"- extractor 报告数量: {int(cross.get('extractor_reported_total') or 0)}")
        lines.append(f"- judge 真实跨实体数量: {int(cross.get('judge_true_cross_total') or 0)}")
        lines.append(f"- 重合数量: {int(cross.get('overlap_total') or 0)}")
        lines.append(f"- 重合率: {_format_float(_score(cross.get('overlap_rate')))}")
        lines.append(f"- precision: {_format_float(_score(cross.get('precision')))}")
        lines.append(f"- recall: {_format_float(_score(cross.get('recall')))}")
    lines.append("")

    lines.append("## Per-Entity 聚合")
    lines.append("")
    lines.append("| Entity | Days | Avg Purity | Avg Completeness |")
    lines.append("|---|---:|---:|---:|")
    per_entity = aggregate.get("per_entity") if isinstance(aggregate, dict) else []
    if isinstance(per_entity, list) and per_entity:
        for row in per_entity:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                "| {entity} | {days} | {purity} | {completeness} |".format(
                    entity=str(row.get("entity_name") or ""),
                    days=int(row.get("days") or 0),
                    purity=_format_float(_score(row.get("avg_entity_purity"))),
                    completeness=_format_float(_score(row.get("avg_entity_completeness"))),
                )
            )
    else:
        lines.append("| - | 0 | 0.000 | 0.000 |")
    lines.append("")

    lines.append("## 失败 Case 列表")
    lines.append("")
    lines.append(f"阈值：entity_purity < {FAIL_THRESHOLD} 或 entity_completeness < {FAIL_THRESHOLD}")
    lines.append("")
    failures = aggregate.get("failure_cases") if isinstance(aggregate, dict) else []
    if isinstance(failures, list) and failures:
        lines.append("| Date | Entity | Purity | Completeness | Wrong Event IDs | Missing Event IDs |")
        lines.append("|---|---|---:|---:|---|---|")
        for item in failures:
            if not isinstance(item, Mapping):
                continue
            wrong = item.get("wrong_event_ids") if isinstance(item.get("wrong_event_ids"), list) else []
            missing = item.get("missing_event_ids") if isinstance(item.get("missing_event_ids"), list) else []
            lines.append(
                "| {date} | {entity} | {purity} | {completeness} | {wrong} | {missing} |".format(
                    date=str(item.get("date") or ""),
                    entity=str(item.get("entity_name") or ""),
                    purity=_format_float(_score(item.get("entity_purity"))),
                    completeness=_format_float(_score(item.get("entity_completeness"))),
                    wrong=", ".join(str(value) for value in wrong),
                    missing=", ".join(str(value) for value in missing),
                )
            )
    else:
        lines.append("- 无")

    return "\n".join(lines).rstrip() + "\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dry_run_day(date: str) -> dict[str, Any]:
    extractor_output = {
        "date": date,
        "entities": [
            {
                "name": "KeyPulse",
                "type": "project",
                "aliases": ["KP"],
                "confidence": 0.95,
                "evidence_event_ids": ["97799"],
            },
            {
                "name": "奇趣宝",
                "type": "project",
                "aliases": [],
                "confidence": 0.93,
                "evidence_event_ids": ["96881"],
            },
        ],
        "event_entity_map": [
            {
                "event_id": "96881",
                "primary_entity": "奇趣宝",
                "secondary_entities": [],
                "confidence": 0.92,
                "needs_review": False,
                "review_reason": "",
            },
            {
                "event_id": "97799",
                "primary_entity": "KeyPulse",
                "secondary_entities": ["News Signal HUD"],
                "confidence": 0.90,
                "needs_review": False,
                "review_reason": "",
            },
            {
                "event_id": "98000",
                "primary_entity": "KeyPulse",
                "secondary_entities": ["奇趣宝"],
                "confidence": 0.54,
                "needs_review": True,
                "review_reason": "同一事件出现两个独立项目活动",
            },
        ],
        "cross_entity_warning": ["event 98000 涉及跨实体活动，建议人审"],
    }

    capped_events = [
        {
            "event_id": "96881",
            "ts_start": f"{date}T09:12:00Z",
            "source": "codex_cli",
            "speaker": "user",
            "app_name": "Cursor",
            "window_title": "奇趣宝 交互流程",
            "content_text": "奇趣宝 PPT 结构与 Agent 确认卡片交互草案。",
            "work_unit": "奇趣宝/交互设计",
            "file_paths": ["/Users/Harland/Go/qiqubao/pitch.md"],
            "urls": [],
        },
        {
            "event_id": "97799",
            "ts_start": f"{date}T14:20:00Z",
            "source": "codex_cli",
            "speaker": "user",
            "app_name": "Cursor",
            "window_title": "News Signal HUD 优先级",
            "content_text": "KeyPulse News Signal HUD 按 P0/P1/P2 拆分展示策略。",
            "work_unit": "keypulse/hud",
            "file_paths": ["/Users/Harland/Go/keypulse/keypulse/hud/monitor_html.py"],
            "urls": [],
        },
        {
            "event_id": "98000",
            "ts_start": f"{date}T16:05:00Z",
            "source": "codex_cli",
            "speaker": "user",
            "app_name": "Cursor",
            "window_title": "跨实体混合讨论",
            "content_text": "同一段里同时讨论奇趣宝确认卡片和 KeyPulse HUD 排期。",
            "work_unit": "mixed",
            "file_paths": [],
            "urls": [],
        },
    ]

    judge_output = {
        "date": date,
        "per_entity": [
            {
                "entity_name": "KeyPulse",
                "entity_purity": {
                    "score": 0.90,
                    "wrong_event_ids": [],
                    "reasoning": "KeyPulse 名下事件多数与 HUD 规划相关，污染较低。",
                },
                "entity_completeness": {
                    "score": 0.88,
                    "missing_event_ids": ["98111"],
                    "reasoning": "仍有一条 KeyPulse 相关事件未被主归属到 KeyPulse。",
                },
            },
            {
                "entity_name": "奇趣宝",
                "entity_purity": {
                    "score": 0.95,
                    "wrong_event_ids": [],
                    "reasoning": "奇趣宝名下事件语义集中，边界清晰。",
                },
                "entity_completeness": {
                    "score": 0.92,
                    "missing_event_ids": [],
                    "reasoning": "奇趣宝核心事件已被覆盖。",
                },
            },
        ],
        "per_day": {
            "cross_entity_warning_precision": {
                "score": 1.0,
                "extractor_reported_event_ids": ["98000"],
                "true_positive_event_ids": ["98000"],
                "false_positive_event_ids": [],
                "reasoning": "唯一告警事件确为跨实体。",
            },
            "cross_entity_warning_recall": {
                "score": 0.5,
                "missed_event_ids": ["98222"],
                "reasoning": "还有一条跨实体事件未被标注 needs_review。",
            },
            "overall_quality": {
                "score": 0.84,
                "reasoning": "整体抽取质量较好，跨实体召回仍有缺口。",
            },
        },
    }

    return {
        "date": date,
        "source_event_count": 3,
        "capped_event_count": 3,
        "was_capped": False,
        "extractor_output": extractor_output,
        "capped_events": capped_events,
        "judge_output": judge_output,
    }


def _run_live_day(date: str, gateway: ModelGateway) -> dict[str, Any]:
    extractor_result = extract_for_date(date)
    extractor_output = extractor_result.to_dict()

    capped_events, source_event_count, was_capped = _load_capped_events_for_date(date)
    judge_output = _judge_day(
        date=date,
        extractor_output=extractor_output,
        capped_events=capped_events,
        gateway=gateway,
    )

    return {
        "date": date,
        "source_event_count": source_event_count,
        "capped_event_count": len(capped_events),
        "was_capped": was_capped,
        "extractor_output": extractor_output,
        "capped_events": [_event_for_judge(event) for event in capped_events],
        "judge_output": judge_output,
    }


def _resolve_dates(raw_dates: list[str], dry_run: bool, dry_run_date: str) -> list[str]:
    if dry_run:
        return [str(dry_run_date or TARGET_DATES[0]).strip()]

    normalized: list[str] = []
    for raw in raw_dates:
        text = str(raw or "").strip()
        if text:
            normalized.append(text)
    if normalized:
        return normalized
    return list(TARGET_DATES)


def main() -> int:
    args = _args()
    dates = _resolve_dates(args.dates, dry_run=bool(args.dry_run), dry_run_date=str(args.dry_run_date or "").strip())

    json_out = Path(args.json_out).expanduser()
    md_out = Path(args.md_out).expanduser()

    days: list[dict[str, Any]] = []

    if args.dry_run:
        days.append(_dry_run_day(dates[0]))
        model_info: dict[str, Any] = {"mode": "dry-run", "model": "stub"}
    else:
        cfg = Config.load()
        init_db(cfg.db_path_expanded)
        gateway = load_model_gateway(cfg)

        for date in dates:
            print(f"[eval_entity_extractor] running date={date}")
            day_result = _run_live_day(date, gateway)
            days.append(day_result)
            print(
                "[eval_entity_extractor] done date={date} capped={capped} source_events={src}"
                .format(
                    date=date,
                    capped=day_result["capped_event_count"],
                    src=day_result["source_event_count"],
                )
            )

        model_info = {
            "mode": "live",
            "active_profile": str(cfg.model.active_profile),
            "cloud_model": str(cfg.model.cloud.model),
            "local_model": str(cfg.model.local.model),
        }

    aggregate = _build_aggregate(days)
    payload: dict[str, Any] = {
        "run_dates": dates,
        "total_days": len(days),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model": model_info,
        "days": days,
        "aggregate": aggregate,
    }

    markdown = _render_markdown(payload)
    _write_json(json_out, payload)
    _write_text(md_out, markdown)

    print(f"[eval_entity_extractor] json={json_out}")
    print(f"[eval_entity_extractor] markdown={md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
