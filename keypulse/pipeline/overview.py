from __future__ import annotations

from keypulse.pipeline.model import ModelGateway, PipelineQualityError
from keypulse.pipeline.thing import Thing


def render_overview(things: list[Thing], model_gateway: ModelGateway | None) -> str:
    if model_gateway is None:
        raise PipelineQualityError("overview: no model gateway")

    prompt = _build_prompt(things)
    try:
        rendered = (model_gateway.render(prompt) or "").strip()
    except Exception as exc:
        raise PipelineQualityError("overview: LLM call failed") from exc

    if not rendered:
        raise PipelineQualityError("overview: empty response")
    return rendered


def _build_prompt(things: list[Thing]) -> str:
    lines: list[str] = []
    for thing in things:
        first_line = ""
        for raw in thing.events:
            text = (raw.intent or "").strip().replace("\n", " ")
            if text:
                first_line = text
                break
        lines.append(f"- {thing.title} | {first_line[:80]}")

    bullets = "\n".join(lines) if lines else "-"
    return (
        "你在给用户写今日做事概览。请基于下面事项，写一段中文今日做事概览。\n\n"
        f"事项列表（标题 | 线索）：\n{bullets}\n\n"
        "要求：\n"
        "- 用「你」称呼用户，语气克制，不写小说\n"
        "- 只保留可验证事实，保留关键细节（commit hash、URL host、应用名、主题词）\n"
        "- 不写心理活动，不写环境描写，不要反问句\n"
        "- **必须分多段**：按主题或时间段拆成 2-4 段，段与段之间用空行分隔，让人扫读不费劲\n"
        "- 不要写成一长段铺平的文字\n"
        "- 不输出标题，不输出列表，纯叙事段落\n\n"
        "示例结构（语气和分段参考）：\n"
        "「上午围绕 KeyPulse 做了管道改造，commit 11a3a9b 砍了 grouper 那一层，新写了 session_renderer。\n\n"
        "中午跟 Codex 协作跑了几轮 e2e 测试，788 个用例都过了。\n\n"
        "下午切到日报路径验证产物，时区修复让 daily 里的事件时间从 UTC 转成北京时间，凌晨忙碌的错觉消除。」"
    )
