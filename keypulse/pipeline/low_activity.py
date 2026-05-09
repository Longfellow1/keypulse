"""低活动周识别（兜底节假日检测）。
M0 只提供函数，M2 让 holiday_strategy 调用。"""

from typing import List


def is_low_activity_week(
    daily_event_counts: List[int],
    historical_avg: float,
    threshold: float = 0.3,
    min_low_days: int = 3,
) -> bool:
    """连续 ≥min_low_days 天事件量 < historical_avg * threshold → True"""
    if not daily_event_counts or historical_avg <= 0:
        return False
    cutoff = historical_avg * threshold
    consec = 0
    max_consec = 0
    for count in daily_event_counts:
        if count < cutoff:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    return max_consec >= min_low_days
