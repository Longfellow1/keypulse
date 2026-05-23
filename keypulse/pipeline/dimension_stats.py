from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_DECISION_TAG_RE = re.compile(r"\[DECISION:\s*(.+?)\]", re.IGNORECASE)
_SHIPPED_TAG_RE = re.compile(r"\[SHIPPED:\s*(.+?)\]", re.IGNORECASE)
_DECISION_SEMANTIC_RE = re.compile(r"决定|选择|拍板|暂停|敲定|定调|确定")
_COLLAB_CLAUDE_RE = re.compile(r"\bclaude\b", re.IGNORECASE)
_COLLAB_CODEX_RE = re.compile(r"\bcodex\b", re.IGNORECASE)
_COLLAB_CHATGPT_RE = re.compile(r"\bchatgpt\b|\bchat\s*gpt\b", re.IGNORECASE)
_COLLAB_HUMAN_RE = re.compile(r"slack|微信|wechat|邮件|email|mail|会议|meeting|message", re.IGNORECASE)
_OUTPUT_DECISION_RE = re.compile(r"commit|pr|merge", re.IGNORECASE)


@dataclass(frozen=True)
class FiveDimensions:
    decisions: int
    advances: int
    new_starts: list[str]
    collaborations: dict[str, int]
    outputs: dict[str, Any]


def _daily_texts(daily_summaries: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for daily in daily_summaries:
        if not isinstance(daily, dict):
            continue
        content = str(daily.get("content_full") or "").strip()
        if content:
            texts.append(content)
        for cluster in daily.get("clusters") or []:
            if isinstance(cluster, dict):
                line = str(cluster.get("narrative_one_line") or "").strip()
                if line:
                    texts.append(line)
    return texts


def _collect_topic_days(daily_summaries: list[dict[str, Any]]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for daily in daily_summaries:
        if not isinstance(daily, dict):
            continue
        day = str(daily.get("date") or "").strip()
        if not day:
            continue
        snapshot = daily.get("topic_status_snapshot") or {}
        if isinstance(snapshot, dict):
            for slug in snapshot.keys():
                key = str(slug).strip()
                if key:
                    mapping.setdefault(key, set()).add(day)
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            key = str(cluster.get("slug") or "").strip()
            if key:
                mapping.setdefault(key, set()).add(day)
    return mapping


def _collect_week_snapshot(daily_summaries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for daily in daily_summaries:
        snapshot = daily.get("topic_status_snapshot") or {}
        if not isinstance(snapshot, dict):
            continue
        for slug, payload in snapshot.items():
            if not isinstance(payload, dict):
                continue
            key = str(slug).strip()
            if not key:
                continue
            merged[key] = payload
    return merged


def _developer_outputs(daily_summaries: list[dict[str, Any]]) -> dict[str, int]:
    shipped_texts: list[str] = []
    for text in _daily_texts(daily_summaries):
        shipped_texts.extend(match.group(1).strip() for match in _SHIPPED_TAG_RE.finditer(text) if match.group(1).strip())

    commits = 0
    tests = 0
    prs = 0
    issues = 0
    lines_added = 0
    lines_deleted = 0
    for text in shipped_texts:
        lowered = text.lower()
        if "commit" in lowered:
            commits += 1
        if "测试" in text or "test" in lowered:
            tests += 1
        if re.search(r"\bpr\b|pull request", lowered):
            prs += 1
        if "issue" in lowered or "bug" in lowered:
            issues += 1
        for plus in re.findall(r"\+(\d+)", text):
            lines_added += int(plus)
        for minus in re.findall(r"-(\d+)", text):
            lines_deleted += int(minus)

    return {
        "commits": commits,
        "tests": tests,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "prs": prs,
        "issues": issues,
    }


def _general_outputs(daily_summaries: list[dict[str, Any]]) -> dict[str, int]:
    count = 0
    for daily in daily_summaries:
        topics = daily.get("topics")
        if isinstance(topics, list):
            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                count += sum(1 for text in (topic.get("shipped") or []) if str(text).strip())
                for text in topic.get("decisions") or []:
                    if _OUTPUT_DECISION_RE.search(str(text or "")):
                        count += 1

    if count > 0:
        return {"saved_files": count}

    cluster_count = 0
    for daily in daily_summaries:
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            cluster_count += int(cluster.get("event_count") or 0)
    return {"saved_files": cluster_count}


def compute_five_dimensions(
    daily_summaries: list[dict[str, Any]],
    previous_week_snapshot: dict[str, Any] | None,
    profile: dict[str, Any],
) -> FiveDimensions:
    texts = _daily_texts(daily_summaries)
    decision_count = sum(len(_DECISION_TAG_RE.findall(text)) for text in texts)
    decision_count += sum(1 for text in texts if _DECISION_SEMANTIC_RE.search(text) and not _DECISION_TAG_RE.search(text))

    topic_days = _collect_topic_days(daily_summaries)
    advances = sum(len(days) for days in topic_days.values() if len(days) >= 2)

    previous = previous_week_snapshot if isinstance(previous_week_snapshot, dict) else {}
    current = _collect_week_snapshot(daily_summaries)
    new_starts: list[str] = []
    for slug, payload in current.items():
        if slug in previous:
            continue
        if str(payload.get("state") or "").strip() == "started":
            new_starts.append(str(payload.get("name") or slug))

    collaborations = {"claude": 0, "codex": 0, "chatgpt": 0, "human_msg": 0}
    for text in texts:
        collaborations["claude"] += len(_COLLAB_CLAUDE_RE.findall(text))
        collaborations["codex"] += len(_COLLAB_CODEX_RE.findall(text))
        collaborations["chatgpt"] += len(_COLLAB_CHATGPT_RE.findall(text))
        collaborations["human_msg"] += len(_COLLAB_HUMAN_RE.findall(text))

    work_type = str((profile or {}).get("work_type") or "general").strip().lower()
    if work_type == "developer":
        outputs = _developer_outputs(daily_summaries)
    else:
        outputs = _general_outputs(daily_summaries)

    return FiveDimensions(
        decisions=decision_count,
        advances=advances,
        new_starts=sorted(set(new_starts)),
        collaborations=collaborations,
        outputs=outputs,
    )


def render_key_data_section(dims: FiveDimensions, style: str) -> str:
    if style != "exec":
        return ""

    new_starts = f"{len(dims.new_starts)} 个" if dims.new_starts else "无"
    collab_parts = []
    if dims.collaborations.get("claude", 0) > 0:
        collab_parts.append(f"Claude × {dims.collaborations['claude']}")
    if dims.collaborations.get("codex", 0) > 0:
        collab_parts.append(f"Codex × {dims.collaborations['codex']}")
    if dims.collaborations.get("chatgpt", 0) > 0:
        collab_parts.append(f"ChatGPT × {dims.collaborations['chatgpt']}")
    if dims.collaborations.get("human_msg", 0) > 0:
        collab_parts.append(f"Human Msg × {dims.collaborations['human_msg']}")
    collab_text = " / ".join(collab_parts) if collab_parts else "无明显协作记录"

    if {"commits", "tests", "lines_added", "lines_deleted", "prs", "issues"}.issubset(set(dims.outputs.keys())):
        outputs_text = (
            f"{int(dims.outputs.get('commits', 0))} commits, "
            f"{int(dims.outputs.get('tests', 0))} 测试, "
            f"+{int(dims.outputs.get('lines_added', 0))}/-{int(dims.outputs.get('lines_deleted', 0))} 行, "
            f"PR {int(dims.outputs.get('prs', 0))}, issue {int(dims.outputs.get('issues', 0))}"
        )
    else:
        outputs_text = f"保存文件 {int(dims.outputs.get('saved_files', 0))} 次"

    lines = [
        "## 关键数据",
        "| 维度 | 数值 |",
        "|---|---|",
        f"| 决策 | {dims.decisions} 次 |",
        f"| 推进 | {dims.advances} topic-days |",
        f"| 新启动 | {new_starts} |",
        f"| 协作 | {collab_text} |",
        f"| 产出 | {outputs_text} |",
    ]
    return "\n".join(lines)
