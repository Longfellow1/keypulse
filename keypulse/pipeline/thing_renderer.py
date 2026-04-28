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
        "你是用户活动总结助手。给定一组关联事件（同一件事的不同侧面：终端命令、git commit、AI 对话、浏览器访问、笔记修改），用四段式描述这件事：\n\n"
        "事件流（按时间排序）：\n"
        f"{event_stream}\n\n"
        "输出 markdown 格式：\n"
        "### <事件主旨标题>\n"
        "- 问题：用户遇到什么问题 / 想做什么（≤30 字）\n"
        "- 做法：用户用了什么方法（≤50 字）\n"
        "- 结论：得出什么结果 / 决策（≤50 字）\n"
        "- 产物：commit hash / 文件路径 / URL / 笔记（最多 3 个，逗号分隔）\n\n"
        "不要复述事件流，要总结。如果某段无法推断，写\"-\"。"
    )


def _fallback(thing: Thing) -> str:
    top_entities = sorted(thing.entities, key=lambda entity: entity.confidence, reverse=True)[:3]
    artifacts = ", ".join(entity.value for entity in top_entities) if top_entities else "-"
    return "\n".join(
        [
            f"### {thing.title}",
            "- 问题：-",
            f"- 做法：涉及 {len(thing.events)} 个事件",
            "- 结论：-",
            f"- 产物：{artifacts}",
        ]
    )
