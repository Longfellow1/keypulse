from __future__ import annotations

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.thing_clusterer import Thing


def render_thing(thing: Thing, *, model_gateway: ModelGateway | None) -> str:
    if model_gateway is None:
        return _fallback(thing)

    prompt = _build_prompt(thing)
    try:
        rendered = (model_gateway.render(prompt) or "").strip()
    except Exception:
        rendered = ""
    return rendered or _fallback(thing)


def _build_prompt(thing: Thing) -> str:
    lines = []
    for event in sorted(thing.events, key=lambda item: item.time):
        intent = (event.intent or "").replace("\n", " ")[:120]
        lines.append(f"- {event.time.isoformat()} | {event.source} | {event.actor} | {intent}")
    event_stream = "\n".join(lines)

    return (
        "你是用户的活动笔友，正在给他写一段日记，让他几个月后回看时能想起当时在干嘛。\n\n"
        "事件流（按时间排序，可能跨多个源——终端、git、AI 对话、浏览、笔记）：\n"
        f"{event_stream}\n\n"
        "输出 markdown 格式：\n"
        "### <一句话名词标题，≤20 字>\n"
        "<一段自然口语叙事，60-150 字>\n\n"
        "叙事要求：\n"
        "- 用「你」称呼用户，像朋友便签的口吻\n"
        "- 不要列表 / 不要「问题/做法/结论」字段\n"
        "- 一段话里把这件事的来龙去脉讲清楚：起因、做了什么、得到什么\n"
        "- 保留关键细节：commit 短 hash、关键文件名、URL host、对话主旨\n"
        "- 不要复述每条事件，要语义合并\n"
        "- 没结论 / 没产物就别强凑，自然提就行（如「留了条点评」「顺手浏览了」这种语气）"
    )


def _fallback(thing: Thing) -> str:
    top_entities = sorted(thing.entities, key=lambda entity: entity.confidence, reverse=True)[:3]
    artifacts = "、".join(entity.value for entity in top_entities) if top_entities else ""
    sources = "、".join(sorted(thing.sources)) if thing.sources else "未知来源"
    detail = f"，主要痕迹是 {artifacts}" if artifacts else ""
    return (
        f"### {thing.title}\n"
        f"这段时间你在 {sources} 上有 {len(thing.events)} 条活动{detail}。"
        f"具体内容 LLM 不可用时无法概括，等下次连通再回看。"
    )
