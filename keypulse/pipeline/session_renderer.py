from __future__ import annotations

import hashlib
import re
from datetime import timedelta, timezone

from keypulse.pipeline.entity_extractor import extract
from keypulse.pipeline.model import ModelGateway, PipelineQualityError
from keypulse.pipeline.session_splitter import ActivitySession
from keypulse.pipeline.thing import Thing


_THING_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_MAX_EVENTS_PER_PROMPT = 50
_LOCAL_TZ = timezone(timedelta(hours=8))


def render_session_things(
    session: ActivitySession,
    *,
    model_gateway: ModelGateway | None,
) -> list[Thing]:
    if model_gateway is None:
        raise PipelineQualityError("session_renderer: no model gateway")

    prompt = _build_prompt(session)
    try:
        rendered = (model_gateway.render(prompt) or "").strip()
    except Exception as exc:
        raise PipelineQualityError("session_renderer: LLM call failed") from exc

    if not rendered:
        raise PipelineQualityError("session_renderer: empty response")

    things = _parse_things(rendered, session)
    if not things:
        raise PipelineQualityError("session_renderer: parse produced 0 things")
    return things


def _build_prompt(session: ActivitySession) -> str:
    events = session.events[:_MAX_EVENTS_PER_PROMPT]
    overflow = len(session.events) - len(events)

    lines: list[str] = []
    for event in events:
        intent = (event.intent or "").replace("\n", " ")[:120]
        local_time = event.time.astimezone(_LOCAL_TZ).isoformat()
        lines.append(f"- {local_time} | {event.source} | {event.actor} | {intent}")
    if overflow > 0:
        lines.append(f"- ... 还有 {overflow} 条事件未列出")
    event_stream = "\n".join(lines)

    return (
        "你在给用户记日记，让他几个月后回看能想起当时干了什么。\n\n"
        "下面是一段连续活动时间内的事件流（按时间排序，可能跨多个源——终端、git、AI 对话、浏览、笔记）：\n"
        f"{event_stream}\n\n"
        "请识别这段时间用户做的 1-7 件具体的事，每件事用一段叙事讲清楚。\n\n"
        "输出 markdown 格式（多段，每段一件事）：\n"
        "### <一句话名词标题，≤20 字>\n"
        "<一段叙事，70-130 字>\n\n"
        "### <下一件事的标题>\n"
        "<下一件事的叙事>\n\n"
        "口吻：朋友给你写的事实笔记，温度有但克制\n"
        "- 用「你」称呼用户\n"
        "- 时间用人话（「下午四点多」「凌晨快一点」），不要 ISO 时间戳\n"
        "- 不出现工程术语（不要「通过 chrome_history 记录」「事件流」「raw_ref」）\n"
        "- 应用名按真名说（Chrome / 微信 / Terminal / Obsidian / Codex 等），不要 bundle id\n"
        "- 必须保留关键细节：commit 短 hash、文件名、URL host、对话主旨、应用名、显著 duration\n"
        "\n"
        "禁止：\n"
        "- 心理活动脑补（「心里咦了一下」「估计在想」「不知道有没有」）\n"
        "- 环境描写（「阳光斜斜地照」「屏幕字符跳来跳去」）\n"
        "- 连发反问（「是不是 X？是不是 Y？」一次都不要）\n"
        "- 颜文字、波浪号、啦呢呀啊呀语气词收尾\n"
        "- 把同一件事拆成多个 ### 段——要语义合并\n"
        "- 把不同事强行合到一段——分组按事的边界，不按时间\n"
        "\n"
        "甜点参考（这是目标语气）：\n"
        "  ✅ 好：「下午四点多，Chrome 提醒收到一封新邮件，你没立刻点开，先把手上的活干完。」\n"
        "  ❌ 太空：「2026-04-27T16:34，你通过 chrome_history 记录收到 1 封新邮件。」\n"
        "  ❌ 太花：「四月底的下午四点半刚过，阳光正好斜斜地照在屏幕上...」\n"
        "\n"
        "段落要把这件事讲清楚：什么时候、做了什么、跟什么有关、有没有结果。没结论就收尾，不强凑。"
    )


def _parse_things(rendered: str, session: ActivitySession) -> list[Thing]:
    headings = list(_THING_HEADING_RE.finditer(rendered))
    if not headings:
        return []

    parsed: list[Thing] = []
    for idx, match in enumerate(headings):
        title = match.group(1).strip()[:200]
        if not title:
            continue
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(rendered)
        narrative = rendered[body_start:body_end].strip()
        if not narrative:
            continue
        parsed.append(_thing_from_session(title, narrative, idx, session))
    return parsed


def _thing_from_session(title: str, narrative: str, idx: int, session: ActivitySession) -> Thing:
    entities_seen: dict[tuple[str, str], object] = {}
    for event in session.events:
        for entity in extract(event):
            key = (entity.kind, entity.value)
            current = entities_seen.get(key)
            if current is None or entity.confidence > current.confidence:  # type: ignore[union-attr]
                entities_seen[key] = entity

    basis = f"{session.id}|{idx}|{title}".encode("utf-8")
    thing_id = hashlib.sha256(basis).hexdigest()[:12]
    return Thing(
        id=thing_id,
        title=title,
        entities=list(entities_seen.values()),  # type: ignore[arg-type]
        events=list(session.events),
        time_start=session.time_start,
        time_end=session.time_end,
        sources={event.source for event in session.events},
        narrative=narrative,
    )
