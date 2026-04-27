from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keypulse.pipeline.hourly import FEW_SHOT, MOTIVES, load_hourly_summaries, parse_json_payload, refresh_hourly_summaries

logger = logging.getLogger(__name__)

_SKELETON_HARD_LIMIT = 15_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _motives_text() -> str:
    return "\n".join(
        f"- {m['id']} ({m['name']})：{m['desc']} 信号：{', '.join(m['signals'])}"
        for m in MOTIVES
    )


def _default_skeleton(date_str: str) -> dict[str, Any]:
    base = round(1.0 / max(len(MOTIVES), 1), 2)
    return {
        "date": date_str,
        "motives": [
            {"id": motive["id"], "confidence": base, "summary": "", "evidence_refs": [], "gap": ""}
            for motive in MOTIVES
        ],
        "main_lines": [],
    }


def _load_existing_skeleton(db_path: Path, date_str: str) -> dict[str, Any] | None:
    conn = _open_conn(db_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM daily_skeletons WHERE date = ?",
            (date_str,),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None
    finally:
        conn.close()


def save_daily_skeleton(
    db_path: Path,
    date_str: str,
    payload: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> None:
    conn = _open_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO daily_skeletons(date, payload_json, generated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                payload_json = excluded.payload_json,
                generated_at = excluded.generated_at
            """,
            (
                date_str,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                generated_at or _utc_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_daily_skeleton(db_path: Path, date_str: str) -> dict[str, Any]:
    payload = _load_existing_skeleton(db_path, date_str)
    return payload if payload is not None else _default_skeleton(date_str)


def _limit_summaries_for_prompt(hourly_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(hourly_summaries) <= 24:
        return list(hourly_summaries)
    step = max(1, len(hourly_summaries) // 12)
    sampled = hourly_summaries[::step][:12]
    if hourly_summaries[-1] not in sampled:
        sampled.append(hourly_summaries[-1])
    return sampled


def build_skeleton_prompt(
    date_str: str,
    hourly_summaries: list[dict[str, Any]],
    prior_skeleton: dict[str, Any],
) -> str:
    compact = _limit_summaries_for_prompt(hourly_summaries)
    hourly_text = json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    prior_text = json.dumps(prior_skeleton, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    prompt = f"""任务：根据当天的 hourly 摘要和“上次骨架”更新 7 个使用动机的置信度。

# 日期
{date_str}

# 7 个动机定义
{_motives_text()}

# Few-shot 示例
{FEW_SHOT}

# 上次骨架
{prior_text}

# 当天 hourly 摘要
{hourly_text}

# 输出格式（严格 JSON，不要 markdown 代码围栏）

{{
  "date": "{date_str}",
  "motives": [
    {{
      "id": "<动机 id>",
      "confidence": <0-1>,
      "summary": "<第二人称一句话>",
      "evidence_refs": [<引用 hourly_summaries 里的 raw_event id>],
      "gap": "<还缺什么证据才能更确定，没缺口写空串>"
    }}
  ],
  "main_lines": ["<2-3 条今日主线，每条一句>"]
}}

要求：
1. 必须基于“上次骨架”做更新，不要把它当作空白重新推断。
2. 只对真有证据支持的动机给较高置信度。
3. 证据稀少时可以保留 prior，但要在 summary/gap 里说明缺口。
4. main_lines 只写 2-3 条最重要主线。"""
    if len(prompt) <= _SKELETON_HARD_LIMIT:
        return prompt

    trimmed = json.dumps(_limit_summaries_for_prompt(compact[:8]), ensure_ascii=False, indent=2, sort_keys=True, default=str)
    prompt = prompt.replace(hourly_text, trimmed)
    return prompt[:_SKELETON_HARD_LIMIT]


def _normalize_skeleton_payload(
    date_str: str,
    payload: dict[str, Any],
    prior_skeleton: dict[str, Any],
) -> dict[str, Any]:
    prior_map = {
        str(item.get("id")): item
        for item in (prior_skeleton.get("motives") or [])
        if isinstance(item, dict)
    }
    raw_map = {
        str(item.get("id")): item
        for item in (payload.get("motives") or [])
        if isinstance(item, dict)
    }
    normalized_motives: list[dict[str, Any]] = []
    for motive in MOTIVES:
        prior_item = prior_map.get(motive["id"], {})
        raw_item = raw_map.get(motive["id"], {})
        try:
            confidence = float(raw_item.get("confidence", prior_item.get("confidence", 0.0)))
        except (TypeError, ValueError):
            confidence = float(prior_item.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))
        evidence_refs = raw_item.get("evidence_refs") or prior_item.get("evidence_refs") or []
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        normalized_motives.append(
            {
                "id": motive["id"],
                "confidence": confidence,
                "summary": str(raw_item.get("summary") or prior_item.get("summary") or "").strip(),
                "evidence_refs": [
                    int(ref)
                    for ref in evidence_refs
                    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit())
                ],
                "gap": str(raw_item.get("gap") or prior_item.get("gap") or "").strip(),
            }
        )

    main_lines = [str(line).strip() for line in (payload.get("main_lines") or []) if str(line).strip()]
    return {"date": date_str, "motives": normalized_motives, "main_lines": main_lines[:3]}


def refresh_daily_skeleton(
    db_path: Path,
    date_str: str,
    gateway: Any,
    *,
    now: datetime | None = None,
    until_hour: int | None = None,
) -> dict[str, Any]:
    refresh_hourly_summaries(db_path, date_str, gateway, until_hour=until_hour, now=now)
    hourly_summaries = load_hourly_summaries(db_path, date_str)
    prior_skeleton = load_daily_skeleton(db_path, date_str)
    if not hourly_summaries:
        if _load_existing_skeleton(db_path, date_str) is None:
            save_daily_skeleton(
                db_path,
                date_str,
                prior_skeleton,
                generated_at=(now or datetime.now(timezone.utc)).isoformat(),
            )
        return prior_skeleton

    prompt = build_skeleton_prompt(date_str, hourly_summaries, prior_skeleton)
    raw_response = gateway.render(prompt).strip()
    if not raw_response:
        raise ValueError("empty skeleton response from gateway")
    parsed = parse_json_payload(raw_response)
    normalized = _normalize_skeleton_payload(date_str, parsed, prior_skeleton)
    save_daily_skeleton(
        db_path,
        date_str,
        normalized,
        generated_at=(now or datetime.now(timezone.utc)).isoformat(),
    )
    return normalized


def _hourly_summary_index(hourly_summaries: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    index: dict[int, list[dict[str, Any]]] = {}
    for summary in hourly_summaries:
        payload = summary.get("payload") or {}
        for ref in payload.get("evidence_refs") or []:
            try:
                ref_id = int(ref)
            except (TypeError, ValueError):
                continue
            index.setdefault(ref_id, []).append(summary)
    return index


def render_daily_skeleton_report(
    report_date_str: str,
    payload: dict[str, Any],
    *,
    hourly_summaries: list[dict[str, Any]] | None = None,
    db_path: Path | None = None,
    date_str: str = "",
) -> str:
    hourly_summaries = hourly_summaries or []
    motive_name = {m["id"]: m["name"] for m in MOTIVES}
    hourly_index = _hourly_summary_index(hourly_summaries)
    timeline_date_str = date_str or report_date_str
    from keypulse.obsidian.exporter import _render_timeline_narrative

    sorted_motives = sorted(
        [item for item in (payload.get("motives") or []) if isinstance(item, dict)],
        key=lambda item: float(item.get("confidence", 0.0) or 0.0),
        reverse=True,
    )

    lines = [f"# {report_date_str} 骨架报告", ""]
    lines.append("## 今日主线")
    main_lines = [str(line).strip() for line in (payload.get("main_lines") or []) if str(line).strip()]
    if main_lines:
        for line in main_lines[:3]:
            lines.append(f"- {line}")
    else:
        lines.append("- 今日没有足够证据生成稳定主线")
    lines.append("")

    lines.append("## 时间线回放")
    timeline_body = _render_timeline_narrative(hourly_summaries, db_path, timeline_date_str)
    if timeline_body.strip():
        lines.extend(timeline_body.splitlines())
    else:
        lines.append("今日没有可回放的原始事件")
    lines.append("")

    lines.append("## 动机骨架")
    for motive in sorted_motives:
        motive_id = str(motive.get("id") or "")
        name = motive_name.get(motive_id, motive_id)
        try:
            confidence = float(motive.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))
        lines.append(f"### {name} {bar} {confidence:.2f}")
        summary = str(motive.get("summary") or "").strip()
        lines.append(f"**{summary or '暂无稳定结论'}**")
        lines.append("")

        evidence_refs = motive.get("evidence_refs") or []
        if evidence_refs:
            seen_refs: set[int] = set()
            used_summaries: set[str] = set()
            evidence_lines: list[str] = []
            for ref in evidence_refs[:5]:
                try:
                    ref_id = int(ref)
                except (TypeError, ValueError):
                    continue
                if ref_id in seen_refs:
                    continue
                seen_refs.add(ref_id)
                linked = hourly_index.get(ref_id, [])
                if linked:
                    linked_summary = str(linked[0]["payload"].get("summary_zh") or "hourly 摘要").strip()
                    if linked_summary in used_summaries:
                        continue
                    used_summaries.add(linked_summary)
                    evidence_lines.append(f"- raw_event #{ref_id}: {linked_summary}")
                else:
                    evidence_lines.append(f"- raw_event #{ref_id}")

            if evidence_lines:
                lines.append("证据：")
                lines.extend(evidence_lines)

        gap = str(motive.get("gap") or "").strip()
        if gap:
            lines.append(f"\n*缺口*：{gap}")
        lines.append("")

    gaps = [
        str(motive.get("gap") or "").strip()
        for motive in sorted_motives
        if str(motive.get("gap") or "").strip()
    ]
    if gaps:
        lines.append("## 缺口与待补")
        for gap in gaps[:5]:
            lines.append(f"- {gap}")

    return "\n".join(lines).strip()


def build_daily_skeleton_report(
    db_path: Path,
    date_str: str,
    gateway: Any,
    *,
    now: datetime | None = None,
    until_hour: int | None = None,
) -> str:
    payload = refresh_daily_skeleton(db_path, date_str, gateway, now=now, until_hour=until_hour)
    hourly_summaries = load_hourly_summaries(db_path, date_str)
    return render_daily_skeleton_report(
        date_str,
        payload,
        hourly_summaries=hourly_summaries,
        db_path=db_path,
        date_str=date_str,
    )
