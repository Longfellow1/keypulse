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
        "你在给用户记日记，让他几个月后回看能想起当时干了什么。\n\n"
        "事件流（按时间排序，可能跨多个源——终端、git、AI 对话、浏览、笔记）：\n"
        f"{event_stream}\n\n"
        "输出 markdown 格式：\n"
        "### <一句话名词标题，≤20 字>\n"
        "<一段简洁叙事，60-100 字>\n\n"
        "硬要求：\n"
        "- 用「你」称呼用户，但**克制**：陈述事实为主，不写小说\n"
        "- **禁止**：心理活动脑补（如「心里咦了一下」「估计在想」）\n"
        "- **禁止**：环境描写（如「阳光斜斜地照」「屏幕字符跳来跳去」）\n"
        "- **禁止**：反问句（如「是不是」「当时是不是对着屏幕愣了下」）\n"
        "- **禁止**：颜文字、波浪号、啦呢呀语气词收尾\n"
        "- **禁止**：复述每条事件，要语义合并\n"
        "- **必须**：保留关键细节（commit 短 hash、文件名、URL host、对话主旨、duration）\n"
        "- **必须**：一段话讲清来龙去脉（起因/做法/结果），没结论就直接收尾，不要强凑\n\n"
        "示例（好的语气）：\n"
        "「你修了 timeline 段隐私回归，commit 11a3a9b，砍了 Today 模块、把时间线并入 Daily，"
        "skeleton.py 用 if False 注释相关代码。后来发现 obsidian-sync.plist 路径没展开 ~，"
        "改完 daemon reload 同步跑通。」"
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
