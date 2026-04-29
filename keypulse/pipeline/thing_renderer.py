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
        "<一段叙事，70-130 字>\n\n"
        "口吻：朋友给你写的事实笔记，温度有但克制\n"
        "- 用「你」称呼用户\n"
        "- 时间用人话（「下午四点多」「凌晨快一点」），不要 ISO 时间戳\n"
        "- 不出现工程术语（不要「通过 chrome_history 记录」「事件流」「raw_ref」）\n"
        "- 应用名按真名说（Chrome / 微信 / Terminal / Obsidian / Codex 等），不要 bundle id\n"
        "- 必须保留关键细节：commit 短 hash、文件名、URL host、对话主旨、应用名、显著 duration\n"
        "\n"
        "禁止：\n"
        "- 心理活动脑补（「心里咦了一下」「估计在想」「不知道有没有」）\n"
        "- 环境描写（「阳光斜斜地照」「屏幕字符跳来跳去」「指尖在键盘上敲」）\n"
        "- 连发反问（「是不是 X？是不是 Y？」一次都不要）\n"
        "- 颜文字、波浪号、啦呢呀啊呀语气词收尾\n"
        "- 复述每条事件——要语义合并成一段叙事\n"
        "\n"
        "甜点参考（这是目标语气）：\n"
        "  ✅ 好：「下午四点多，Chrome 提醒收到一封新邮件，你没立刻点开，先把手上的活干完。」\n"
        "  ❌ 太空：「2026-04-27T16:34，你通过 chrome_history 记录收到 1 封新邮件。」\n"
        "  ❌ 太花：「四月底的下午四点半刚过，阳光正好斜斜地照在屏幕上...」\n"
        "\n"
        "完整甜点示例：\n"
        "「凌晨一点多还在折腾 KeyPulse 的 timeline 段，commit 11a3a9b 把 Today 模块砍了，时间线并入 Daily，"
        "skeleton.py 里用 if False 注释了相关段。后来发现 obsidian-sync.plist 路径没展开 ~，"
        "改成绝对路径 daemon reload 才跑通同步。」\n"
        "\n"
        "段落要把这件事讲清楚：什么时候、做了什么、跟什么有关、有没有结果。没结论就收尾，不强凑。"
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
