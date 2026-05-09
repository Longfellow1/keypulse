"""Rollup orchestrator facade - 抽象周/月/季/年报告。
M0 只支持 period='week'，委托给 weekly_orchestrator.run_weekly。
M4 后期实现 month/quarter/year。"""

from typing import Literal

from keypulse.pipeline import weekly_orchestrator

Period = Literal["week", "month", "quarter", "year"]


def run_rollup(period: Period, period_str: str, *, style: str = "exec") -> str:
    if period == "week":
        return weekly_orchestrator.run_weekly(period_str, style=style)
    raise NotImplementedError(f"rollup period={period} not implemented yet (M4)")
