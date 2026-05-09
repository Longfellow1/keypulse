from __future__ import annotations

from keypulse.pipeline.holiday_strategy import HolidayContext


def build_holiday_system_injection(ctx: HolidayContext) -> str:
    if ctx.template == "standard":
        return ""

    names = "、".join(ctx.holiday_names) if ctx.holiday_names else "假期"
    if ctx.template == "still_working_in_holiday":
        return f"本周覆盖 {names}，但用户仍有高活动。叙事重点：'假期里你还在 X' 这种语义。"
    if ctx.template == "holiday_rest":
        return f"本周是 {names} 假期，活动量正常下降。TL;DR 直接说休息。不要强造关键进展。"
    return "本周节奏放缓，未识别假期。叙事可问：'是不是有什么没接住？' 推测 dropped_balls 累积。"
