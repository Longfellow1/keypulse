"""Daily 5 类断言 validator — 设计文档 docs/golden-daily/SCHEMA.md。

复用 weekly_validator.classify_sentence / find_evidence。
配套黄金 docs/golden-daily/2026-05-{06,09}.md。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from keypulse.pipeline.weekly_validator import (
    classify_sentence,
    find_evidence,
)


@dataclass
class DailyValidationFailure:
    rule: str           # structure / coverage / texture / antipattern / objectivity
    severity: str       # error / warn
    message: str
    location: str       # 段名或 cluster_id


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DECISION_RE = re.compile(
    r"决定|选择|拍板|暂停|敲定|定调|确定|明确|按.+顺序|"
    r"决策|采用|提出|策略|规划|方案|定为|划分|确认"
)
_OUTPUT_RE = re.compile(
    r"push|commit|发布|上线|接通|完成|落地|提交|合并|修复|merge|创建|"
    r"设计|验证|部署|优化|重构|扩充|解决|添加|处理|生成|更新|集成|实现",
    re.IGNORECASE,
)

_GENERIC_REPEATS = [
    re.compile(r"进入可继续迭代阶段"),
    re.compile(r"把.+整理成下周可复现的执行入口"),
    re.compile(r"暂无可确认的"),
    re.compile(r"本周.+有连续记录"),
    re.compile(r"已形成可复用的周报素材"),
    re.compile(r"沉淀到可复用文档或检查项"),
    re.compile(r"虽然.+但.+推进了"),
]

_ACTION_OBJECT_TITLE_RE = re.compile(
    r"^(修改|查看|浏览|登录|启动|设置|配置|打开|访问|抓取|刷新|检查|连接|执行|输入)"
    r"[A-Za-z0-9一-龥/. _-]+$"
)

_REQUIRED_SECTIONS = ("今日要点",)
_RECOMMENDED_SECTIONS = ("今天做的事", "今日做的事")


def _collect_headings(markdown: str) -> list[dict[str, Any]]:
    headings = []
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        headings.append({
            "level": len(m.group(1)),
            "title": m.group(2).strip(),
            "line": idx,
        })
    return headings


def _topic_segments(markdown: str) -> list[dict[str, Any]]:
    """从 '今天做的事' / '今日做的事' 段下抽出 ### 主题段。"""
    headings = _collect_headings(markdown)
    lines = markdown.splitlines()
    things_start_line = None
    things_level = None
    things_end_line = len(lines)
    for i, h in enumerate(headings):
        title = h["title"]
        if h["level"] in (1, 2) and ("今天做的事" in title or "今日做的事" in title):
            things_start_line = h["line"]
            things_level = h["level"]
            for nxt in headings[i + 1:]:
                if nxt["level"] <= things_level:
                    things_end_line = nxt["line"]
                    break
            break
    if things_start_line is None:
        return []
    segments = []
    sub_level = (things_level or 2) + 1
    for i, h in enumerate(headings):
        if h["level"] != sub_level:
            continue
        if h["line"] <= things_start_line or h["line"] >= things_end_line:
            continue
        body_start = h["line"] + 1
        body_end = things_end_line
        for nxt in headings[i + 1:]:
            if nxt["line"] >= things_end_line:
                break
            body_end = nxt["line"]
            break
        body = "\n".join(lines[body_start:body_end]).strip()
        segments.append({"title": h["title"], "body": body})
    return segments


def _validate_structure(
    markdown: str,
    daily_summary: dict | None,
    failures: list[DailyValidationFailure],
) -> None:
    headings = _collect_headings(markdown)
    titles = [h["title"] for h in headings]

    for required in _REQUIRED_SECTIONS:
        if not any(required in t for t in titles):
            failures.append(DailyValidationFailure(
                rule="structure",
                severity="error",
                message=f"缺必备段: {required}",
                location="markdown",
            ))

    if not any(any(kw in t for kw in _RECOMMENDED_SECTIONS) for t in titles):
        failures.append(DailyValidationFailure(
            rule="structure",
            severity="error",
            message="缺 '今天做的事' / '今日做的事' 段",
            location="markdown",
        ))

    if daily_summary is not None:
        for key in ("topics", "events"):
            if key not in daily_summary:
                failures.append(DailyValidationFailure(
                    rule="structure",
                    severity="error",
                    message=f"daily_summary 缺字段: {key}",
                    location="daily_summary",
                ))


def _validate_coverage(
    markdown: str,
    daily_summary: dict | None,
    failures: list[DailyValidationFailure],
) -> None:
    segments = _topic_segments(markdown)
    if not segments:
        failures.append(DailyValidationFailure(
            rule="coverage",
            severity="error",
            message="'今天做的事' 段下没有 ### 主题段",
            location="今天做的事",
        ))
    for seg in segments:
        body = seg["body"]
        if len(body) < 80:
            failures.append(DailyValidationFailure(
                rule="coverage",
                severity="error",
                message=f"主题段叙事 < 80 字 ({len(body)})",
                location=seg["title"],
            ))
        has_decision = bool(_DECISION_RE.search(body))
        has_output = bool(_OUTPUT_RE.search(body))
        if not (has_decision or has_output):
            failures.append(DailyValidationFailure(
                rule="coverage",
                severity="error",
                message="主题段缺决策或可见产出（'决定/拍板/敲定' 或 'commit/落地/提交' 任一）",
                location=seg["title"],
            ))

    if daily_summary is not None:
        topics = daily_summary.get("topics") or []
        if not isinstance(topics, list) or len(topics) == 0:
            failures.append(DailyValidationFailure(
                rule="coverage",
                severity="error",
                message="daily_summary.topics 为空",
                location="topics",
            ))
        for i, t in enumerate(topics):
            if not isinstance(t, dict):
                failures.append(DailyValidationFailure(
                    rule="coverage", severity="error",
                    message=f"topics[{i}] 不是 dict", location=f"topics[{i}]",
                ))
                continue
            if not t.get("anchor"):
                failures.append(DailyValidationFailure(
                    rule="coverage", severity="error",
                    message=f"topics[{i}] 缺 anchor 字段", location=f"topics[{i}]",
                ))
            narrative = str(t.get("narrative") or "")
            if len(narrative) < 80:
                failures.append(DailyValidationFailure(
                    rule="coverage", severity="error",
                    message=f"topics[{i}] narrative < 80 字 ({len(narrative)})",
                    location=f"topics[{i}]",
                ))
            has_dec = bool(t.get("decisions"))
            has_ship = bool(t.get("shipped"))
            if not (has_dec or has_ship):
                failures.append(DailyValidationFailure(
                    rule="coverage", severity="error",
                    message=f"topics[{i}] 缺 decisions 或 shipped",
                    location=f"topics[{i}]",
                ))

        events = daily_summary.get("events") or []
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                continue
            if "anchored_to" not in e:
                failures.append(DailyValidationFailure(
                    rule="coverage", severity="warn",
                    message=f"events[{i}] 缺 anchored_to 字段（null 也要显式标）",
                    location=f"events[{i}].{e.get('cluster_id', '?')}",
                ))


def _validate_texture(
    markdown: str,
    failures: list[DailyValidationFailure],
) -> None:
    segments = _topic_segments(markdown)
    for seg in segments:
        body = seg["body"]
        has_objective_signal = bool(
            _NUMBER_RE.search(body) or _DATE_RE.search(body)
        )
        if not has_objective_signal:
            failures.append(DailyValidationFailure(
                rule="texture", severity="warn",
                message="主题段无数字/日期等客观锚点",
                location=seg["title"],
            ))


def _strip_code_and_quotes(text: str) -> str:
    """去掉 ``` 代码块、`inline code`、引号包裹的内容（避免误杀引用/示范）。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r'"[^"\n]+"', " ", text)
    text = re.sub(r"[“”][^“”\n]+[“”]", " ", text)
    return text


def _validate_antipattern(
    markdown: str,
    daily_summary: dict | None,
    failures: list[DailyValidationFailure],
) -> None:
    cleaned = _strip_code_and_quotes(markdown)
    for pat in _GENERIC_REPEATS:
        m = pat.search(cleaned)
        if m:
            failures.append(DailyValidationFailure(
                rule="antipattern", severity="error",
                message=f"出现禁用复读句: '{m.group(0)[:40]}'",
                location="markdown",
            ))

    segments = _topic_segments(markdown)
    for seg in segments:
        title = seg["title"].split("|")[0].strip()
        title = re.sub(r"^[✅❌⚠️📌🔥]\s*", "", title)
        if _ACTION_OBJECT_TITLE_RE.match(title):
            failures.append(DailyValidationFailure(
                rule="antipattern", severity="error",
                message=f"主题段标题是'动作+对象'流水账名: '{title}'（应该是主线名）",
                location=seg["title"],
            ))

    _check_h3_truncation(segments, failures)
    _check_duplicate_bullets(segments, failures)

    if daily_summary is not None:
        events = daily_summary.get("events") or []
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                continue
            anchored = e.get("anchored_to")
            event_count = int(e.get("event_count") or 0)
            if anchored and event_count == 1:
                failures.append(DailyValidationFailure(
                    rule="antipattern", severity="error",
                    message=f"events[{i}] event_count=1 却 anchored 到 '{anchored}'（单 event 不应升格）",
                    location=f"events[{i}].{e.get('cluster_id', '?')}",
                ))

        topics = daily_summary.get("topics") or []
        if len(events) >= 5 and len(topics) == 0:
            failures.append(DailyValidationFailure(
                rule="antipattern", severity="error",
                message=f"events={len(events)} >= 5 但 topics 为空（Bug A：LLM anchor 全判 unanchored）",
                location="daily_summary",
            ))


_SENTENCE_ENDS = "。！？.!?"


def _check_h3_truncation(segments: list[dict[str, Any]], failures: list[DailyValidationFailure]) -> None:
    """H3 段尾巴恰好被 120 字索引拼接截断的特征：
    - 段末字符不是句号类
    - 段长 == 120 或多个 120 拼接（120 / 240 / 360 字附近 ±5）
    """
    for seg in segments:
        body = seg["body"].strip()
        if not body or len(body) < 100:
            continue
        last_char = body.rstrip()[-1:]
        if last_char in _SENTENCE_ENDS:
            continue
        ratio = len(body) / 120
        near_multiple = abs(ratio - round(ratio)) * 120 <= 5
        if near_multiple and round(ratio) >= 1:
            failures.append(DailyValidationFailure(
                rule="antipattern", severity="error",
                message=f"主题段疑似被 120 字索引截断 (len={len(body)}, 末字符='{last_char}')",
                location=seg["title"],
            ))


_DUP_BULLET_RE = re.compile(r"^-\s+([^:：]+)[:：]\s*(.+)$")


def _check_duplicate_bullets(segments: list[dict[str, Any]], failures: list[DailyValidationFailure]) -> None:
    """H3 段下出现 '- 标题: 同正文' 形态的重复 bullet（旧 renderer 残留）。"""
    for seg in segments:
        body = seg["body"]
        body_lines = [ln for ln in body.splitlines() if ln.strip()]
        if len(body_lines) < 2:
            continue
        paragraph = body_lines[0].strip()
        for ln in body_lines[1:]:
            m = _DUP_BULLET_RE.match(ln.strip())
            if not m:
                continue
            bullet_body = m.group(2).strip()
            if len(bullet_body) >= 30 and bullet_body[:30] == paragraph[:30]:
                failures.append(DailyValidationFailure(
                    rule="antipattern", severity="error",
                    message=f"主题段下出现重复 bullet（旧 renderer 残留）: '{ln.strip()[:50]}'",
                    location=seg["title"],
                ))
                break


def _validate_objectivity(
    markdown: str,
    daily_summary: dict | None,
    failures: list[DailyValidationFailure],
) -> None:
    if daily_summary is None:
        return
    events = daily_summary.get("events") or []
    corpus_parts = []
    for e in events:
        if isinstance(e, dict):
            corpus_parts.append(str(e.get("narrative_one_line") or ""))
            corpus_parts.append(str(e.get("display_name") or ""))
    corpus = "\n".join(corpus_parts)
    if not corpus:
        return
    topics = daily_summary.get("topics") or []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            continue
        narrative = str(t.get("narrative") or "")
        sentences = re.split(r"[。！？；\n]+", narrative)
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if classify_sentence(s) != "fact":
                continue
            for num_match in _NUMBER_RE.finditer(s):
                num = num_match.group(0)
                level = find_evidence(num, corpus, {})
                if level == "missing":
                    failures.append(DailyValidationFailure(
                        rule="objectivity", severity="warn",
                        message=f"topics[{i}] 数字 '{num}' 在 events corpus 中未溯源",
                        location=f"topics[{i}]",
                    ))


def validate_daily_output(
    *,
    daily_summary: dict | None = None,
    rendered_markdown: str,
    previous_day_anchors: list[str] | None = None,
) -> list[DailyValidationFailure]:
    """5 类断言：结构 → 覆盖 → 质感 → 反模式 → 客观性。

    daily_summary=None 时只验渲染层；提供 daily_summary 时验数据+渲染。
    """
    failures: list[DailyValidationFailure] = []
    _validate_structure(rendered_markdown, daily_summary, failures)
    _validate_coverage(rendered_markdown, daily_summary, failures)
    _validate_texture(rendered_markdown, failures)
    _validate_antipattern(rendered_markdown, daily_summary, failures)
    _validate_objectivity(rendered_markdown, daily_summary, failures)
    return failures


def quick_score(failures: list[DailyValidationFailure]) -> int:
    """简单计分：每个 error -10, 每个 warn -3, 满分 100, 下限 0。"""
    score = 100
    for f in failures:
        score -= 10 if f.severity == "error" else 3
    return max(score, 0)
