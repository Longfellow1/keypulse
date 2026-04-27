from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MOTIVES = [
    {
        "id": "create",
        "name": "创造",
        "desc": "产出新东西。写代码、写文档、画图、做设计、写下原本不存在的内容。",
        "signals": ["代码编辑", "新文件", "长文输入", "git commit", "画图工具"],
    },
    {
        "id": "understand",
        "name": "理解",
        "desc": "吸收信息以建立认知。读、查、研究、问 AI、看文档、读代码。",
        "signals": ["浏览网页", "看文档", "翻 PR/code", "和 AI 问答", "搜索"],
    },
    {
        "id": "communicate",
        "name": "沟通",
        "desc": "和他人交换信息。聊天、邮件、视频会议、回复消息。",
        "signals": ["IM 应用", "邮件", "会议软件", "群聊"],
    },
    {
        "id": "decide",
        "name": "决策",
        "desc": "在多个选项里拍板。对比、评估、权衡、最后选一个方案。",
        "signals": ["A/B/C 选项讨论", "trade-off 关键词", "对比文档", "选型表"],
    },
    {
        "id": "maintain",
        "name": "维护",
        "desc": "处理已有的事。修 bug、整理文件、重启服务、回邮件、更新工具。",
        "signals": ["debug 命令", "log 查看", "tail/grep", "shell 操作", "fix"],
    },
    {
        "id": "transact",
        "name": "事务",
        "desc": "日常处理。买东西、订票、查账、报销、填表。",
        "signals": ["购物站点", "订票/订房", "银行/支付", "表单"],
    },
    {
        "id": "leisure",
        "name": "消遣",
        "desc": "放松/娱乐。刷视频、看推、玩游戏、社交。",
        "signals": ["视频网站", "微博/Twitter", "游戏", "短视频"],
    },
]

FEW_SHOT = """
[示例 1]
活动：01:15-01:21 在终端 SSH 到远程机器，跑 ./stats.sh，输出 "No such file or directory"，复制了一段
log 路径 /mnt/paas/.../feishu_agent.log
判定：
  - maintain: 0.85（在调一个已有项目 Code-RAGFlow 的运行状态，复制 log 路径准备查看）
  - understand: 0.5（试图弄清现状，但还没成型）
  - 其他: 低

[示例 2]
活动：09:46 在 Chrome 里打开 Claude 对话，连续 6 次输入"Haiku 给的研究很扎实，我把它压成对我们落地最关键的判断"
判定：
  - decide: 0.7（在做"用什么落地"的决策，trade-off 关键词出现）
  - understand: 0.6（在消化 Haiku 给的内容）
  - communicate: 0.4（和 AI 协作）

[示例 3]
活动：14:00-15:30 在 VSCode 里 fragments.py 大段编辑，期间 git commit 2 次："fix(evidence)" 和 "feat(pipeline)"
判定：
  - create: 0.9（产出新代码，commit 是硬证据）
  - maintain: 0.5（其中一个是 fix）
  - 其他: 低
"""

_HOURLY_HARD_LIMIT = 10_000
_MAX_ACTIVITY_BUCKETS = 24


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_day_rows(db_path: Path, date_str: str) -> list[dict[str, Any]]:
    conn = _open_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM raw_events
            WHERE ts_start LIKE ?
              AND content_text IS NOT NULL
              AND length(content_text) >= 5
            ORDER BY ts_start ASC
            """,
            (f"{date_str}%",),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _load_existing_hours(db_path: Path, date_str: str) -> set[int]:
    conn = _open_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT hour FROM hourly_summaries WHERE date = ?",
            (date_str,),
        ).fetchall()
        return {int(row["hour"]) for row in rows}
    finally:
        conn.close()


def _limit_items(items: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    if len(items) <= max_items:
        return list(items)
    if max_items <= 1:
        return [items[0]]
    step = max(1, len(items) // max_items)
    return items[::step][:max_items]


def aggregate_hourly_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        ts_text = str(row.get("ts_start") or "")
        if not ts_text:
            continue
        ts = _parse_ts(ts_text)
        bucket_minute = ts.minute - (ts.minute % 5)
        bucket_label = ts.strftime(f"%H:{bucket_minute:02d}")
        app_name = str(row.get("app_name") or "-")
        key = (bucket_label, app_name)
        bucket = buckets.setdefault(
            key,
            {
                "ts": bucket_label,
                "app": app_name,
                "types": set(),
                "samples": [],
                "title": str(row.get("window_title") or ""),
            },
        )
        bucket["types"].add(str(row.get("event_type") or ""))
        snippet = str(row.get("content_text") or "").replace("\n", " ").strip()[:60]
        if snippet and snippet not in bucket["samples"]:
            bucket["samples"].append(snippet)

    activities: list[dict[str, Any]] = []
    for idx, (_, bucket) in enumerate(sorted(buckets.items())):
        activities.append(
            {
                "idx": idx,
                "ts": bucket["ts"],
                "app": bucket["app"],
                "types": sorted(t for t in bucket["types"] if t),
                "title": bucket["title"][:40],
                "samples": bucket["samples"][:3],
            }
        )
    return _limit_items(activities, _MAX_ACTIVITY_BUCKETS)


def _render_activity_lines(activities: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for activity in activities:
        type_str = "/".join(
            t.replace("_capture", "").replace("_copy", "")
            for t in activity.get("types", [])
        )
        samples_str = " | ".join(str(s) for s in activity.get("samples", []))
        lines.append(
            f"[{activity.get('idx')}] {activity.get('ts')} app={activity.get('app')} ({type_str}): {samples_str}"
        )
    return "\n".join(lines)


def _motives_text() -> str:
    return "\n".join(
        f"- {m['id']} ({m['name']})：{m['desc']} 信号：{', '.join(m['signals'])}"
        for m in MOTIVES
    )


def build_hourly_prompt(date_str: str, hour: int, activities: list[dict[str, Any]]) -> str:
    compact_activities = _limit_items(activities, _MAX_ACTIVITY_BUCKETS)
    activity_lines = _render_activity_lines(compact_activities)
    prompt = f"""任务：根据下面某一小时的电脑活动证据，对 7 个"使用动机"假设做结构化标注。

# 日期与小时
{date_str} {hour:02d}:00-{hour:02d}:59

# 7 个动机假设
{_motives_text()}

# Few-shot 示例（看动机怎么打分）
{FEW_SHOT}

# 这一小时的活动（每条编号为 [idx]）
{activity_lines}

# 输出格式（严格 JSON，不要 markdown 代码围栏）

{{
  "hour": "{date_str}T{hour:02d}",
  "topics": ["<最多 5 个主题>"],
  "tools": ["<最多 5 个工具>"],
  "people": ["<最多 3 个相关人物>"],
  "motive_hints": {{
    "create": 0.0,
    "understand": 0.0,
    "communicate": 0.0,
    "decide": 0.0,
    "maintain": 0.0,
    "transact": 0.0,
    "leisure": 0.0
  }},
  "evidence_refs": [<引用上面的 idx>],
  "summary_zh": "<一句中文总结这一小时在做什么>"
}}

要求：
1. motive_hints 的值必须是 0-1 小数。
2. evidence_refs 只引用真支持当前判断的 idx。
3. 证据稀少时降低置信度，不要硬凑。
4. summary_zh 要短，尽量一句话。"""

    if len(prompt) <= _HOURLY_HARD_LIMIT:
        return prompt

    reduced = _limit_items(compact_activities, 12)
    reduced_lines = _render_activity_lines(reduced)
    prompt = prompt.replace(activity_lines, reduced_lines)
    if len(prompt) <= _HOURLY_HARD_LIMIT:
        return prompt

    lighter = [{**activity, "samples": activity.get("samples", [])[:1]} for activity in reduced]
    prompt = prompt.replace(reduced_lines, _render_activity_lines(lighter))
    return prompt[:_HOURLY_HARD_LIMIT]


def _extract_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("no JSON object found in response")
    return text[start : end + 1]


def parse_json_payload(raw_text: str) -> dict[str, Any]:
    cleaned = _extract_json_text(raw_text)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    return payload


def _normalize_motive_hints(raw_hints: Any) -> dict[str, float]:
    hints: dict[str, float] = {m["id"]: 0.0 for m in MOTIVES}
    if isinstance(raw_hints, dict):
        for motive in hints:
            try:
                hints[motive] = max(0.0, min(1.0, float(raw_hints.get(motive, 0.0))))
            except (TypeError, ValueError):
                hints[motive] = 0.0
    return hints


def _normalize_hourly_payload(
    date_str: str,
    hour: int,
    payload: dict[str, Any],
    activities: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_refs = payload.get("evidence_refs") or []
    if not isinstance(raw_refs, list):
        raw_refs = []
    evidence_refs: list[int] = []
    for value in raw_refs:
        try:
            evidence_refs.append(int(value))
        except (TypeError, ValueError):
            continue

    return {
        "date": date_str,
        "hour": hour,
        "topics": [str(v) for v in (payload.get("topics") or []) if str(v).strip()][:5],
        "tools": [str(v) for v in (payload.get("tools") or []) if str(v).strip()][:5],
        "people": [str(v) for v in (payload.get("people") or []) if str(v).strip()][:3],
        "motive_hints": _normalize_motive_hints(payload.get("motive_hints")),
        "evidence_refs": evidence_refs,
        "summary_zh": str(payload.get("summary_zh") or "").strip() or "本小时没有可用证据",
        "activity_count": len(activities),
    }


def load_hourly_summaries(db_path: Path, date_str: str) -> list[dict[str, Any]]:
    conn = _open_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM hourly_summaries WHERE date = ? ORDER BY hour ASC",
            (date_str,),
        ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            summaries.append(
                {
                    "date": row["date"],
                    "hour": int(row["hour"]),
                    "payload": json.loads(row["payload_json"]),
                    "generated_at": row["generated_at"],
                }
            )
        return summaries
    finally:
        conn.close()


def save_hourly_summary(
    db_path: Path,
    date_str: str,
    hour: int,
    payload: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> None:
    conn = _open_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hourly_summaries(date, hour, payload_json, generated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date, hour) DO UPDATE SET
                payload_json = excluded.payload_json,
                generated_at = excluded.generated_at
            """,
            (
                date_str,
                hour,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                generated_at or _utc_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def refresh_hourly_summaries(
    db_path: Path,
    date_str: str,
    gateway: Any,
    *,
    until_hour: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    rows = _load_day_rows(db_path, date_str)
    if not rows:
        return []

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts_text = str(row.get("ts_start") or "")
        if not ts_text.startswith(date_str):
            continue
        ts = _parse_ts(ts_text)
        if until_hour is not None and ts.hour > until_hour:
            continue
        grouped[ts.hour].append(row)

    if not grouped:
        return []

    existing_hours = _load_existing_hours(db_path, date_str)
    generated: list[dict[str, Any]] = []
    for hour in sorted(grouped):
        if hour in existing_hours:
            continue
        activities = aggregate_hourly_events(grouped[hour])
        if not activities:
            continue
        prompt = build_hourly_prompt(date_str, hour, activities)
        raw_response = gateway.render(prompt).strip()
        if not raw_response:
            raise ValueError("empty hourly response from gateway")
        payload = parse_json_payload(raw_response)
        normalized = _normalize_hourly_payload(date_str, hour, payload, activities)
        save_hourly_summary(
            db_path,
            date_str,
            hour,
            normalized,
            generated_at=(now or datetime.now(timezone.utc)).isoformat(),
        )
        generated.append(normalized)
    return generated
