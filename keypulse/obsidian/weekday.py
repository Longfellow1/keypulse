from __future__ import annotations

from datetime import datetime

from keypulse.i18n import current_lang


def weekday_label(date_str: str) -> str:
    if current_lang() == "zh":
        labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        fallback = "今天"
    else:
        labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        fallback = "Today"
    try:
        return labels[datetime.fromisoformat(date_str).weekday()]
    except Exception:
        return fallback
