from __future__ import annotations

import re
from dataclasses import dataclass

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.session_splitter import ActivitySession


@dataclass(frozen=True)
class ThingOutline:
    title: str
    summary_hint: str


def build_outline_prompt(session: ActivitySession) -> str:
    """Build one prompt for one activity session."""
    max_events = 50
    lines: list[str] = []
    for event in session.events[:max_events]:
        summary = ((event.intent or "").strip() or (event.artifact or "").strip() or (event.raw_ref or "").strip()).replace("\n", " ")
        lines.append(f"{event.time.isoformat()} | {event.source} | {event.actor} | {summary[:80]}")
    if len(session.events) > max_events:
        lines.append(f"... 还有 {len(session.events) - max_events} 条")
    event_block = "\n".join(lines) if lines else "-"

    return (
        "你是用户活动总结助手。下面是用户在一段连续活动时间内的事件流。\n\n"
        f"时间窗：{session.time_start.isoformat()} 到 {session.time_end.isoformat()}\n"
        f"事件数：{len(session.events)}\n\n"
        "事件摘要（按时间排序，每行：时间 | 来源 | 主体 | 简述）：\n"
        f"{event_block}\n\n"
        "请识别用户在这段时间做的 3-7 件具体的事（不要写“使用了某应用”这种笼统类别）。\n\n"
        "输出格式（每行一件事）：\n"
        "1. <事件主旨标题（≤30 字）> | <一句话内容提示（≤50 字）>\n"
        "2. ...\n\n"
        "要求：\n"
        "- 事件单元是“具体的事”（如“修了 timeline 段隐私回归”而不是“做代码”）\n"
        "- 一件事可以跨越多个事件源（git commit + 对话 + 文件修改 = 同一件事）\n"
        "- 不要复述事件流，要做语义合并\n"
        "- 标题用名词短语"
    )


def parse_outline_response(response: str) -> list[ThingOutline]:
    """Parse one-outline-per-line response."""
    outlines: list[ThingOutline] = []
    for raw_line in response.splitlines():
        line = re.sub(r"^\s*\d+\s*[.)]\s*", "", raw_line.strip())
        if not line:
            continue
        if "|" in line:
            title, summary_hint = line.split("|", 1)
        else:
            title, summary_hint = line, ""
        title_clean = title.strip()[:30]
        summary_clean = summary_hint.strip()[:50]
        if title_clean:
            outlines.append(ThingOutline(title=title_clean, summary_hint=summary_clean))
    return outlines or [ThingOutline(title="活动 session", summary_hint="")]


def request_outline(
    session: ActivitySession,
    *,
    model_gateway: ModelGateway | None,
) -> list[ThingOutline]:
    """Prompt -> LLM render -> parse with non-throwing fallback."""
    fallback = _session_fallback_outline(session)
    if model_gateway is None:
        return fallback
    prompt = build_outline_prompt(session)
    try:
        response = (model_gateway.render(prompt) or "").strip()
    except Exception:
        return fallback
    if not response:
        return fallback
    outlines = parse_outline_response(response)
    if outlines == [ThingOutline(title="活动 session", summary_hint="")]:
        return fallback
    return outlines


def _session_fallback_outline(session: ActivitySession) -> list[ThingOutline]:
    suffix = session.id.split("-")[-1] if session.id else "1"
    return [ThingOutline(title=f"活动 session {suffix}", summary_hint=f"{len(session.events)} 个事件")]
