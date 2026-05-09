from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from importlib import resources
import json
from pathlib import Path
from typing import Any, Literal

import yaml

from keypulse.pipeline.low_activity import is_low_activity_week


DayLabel = Literal["workday_high", "workday_low", "holiday_high", "holiday_low"]
WeekTemplate = Literal["standard", "still_working_in_holiday", "holiday_rest", "slow_rhythm"]


@dataclass(frozen=True)
class HolidayContext:
    week_str: str
    day_labels: dict[str, DayLabel]
    template: WeekTemplate
    holiday_names: list[str]
    user_marked_reason: str | None
    is_low_activity: bool


def load_holidays_table(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        raw = path.read_text(encoding="utf-8")
    else:
        raw = resources.files("keypulse.data").joinpath("holidays.yaml").read_text(encoding="utf-8")
    payload = yaml.safe_load(raw) or {}
    return payload if isinstance(payload, dict) else {}


def _normalize_religion(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "muslim": "islamic",
        "islam": "islamic",
        "hindu": "hindu",
        "jewish": "jewish",
        "orthodox": "orthodox",
        "unspecified": "",
    }
    return aliases.get(text, text)


def _region_match(record_region: Any, target_region: str) -> bool:
    region = str(target_region or "").strip().upper()
    if isinstance(record_region, list):
        values = {str(item).strip().upper() for item in record_region}
    else:
        values = {str(record_region or "").strip().upper()}
    if "GLOBAL" in values:
        return True
    return region in values


def get_active_holidays(
    week_dates: list[str],
    region: str,
    religion: str = "",
    holidays_table: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    table = holidays_table or load_holidays_table()
    holidays = table.get("holidays") if isinstance(table, dict) else None
    if not isinstance(holidays, list):
        return []

    normalized_religion = _normalize_religion(religion)
    target_dates = {str(day).strip() for day in week_dates if str(day).strip()}
    active: list[dict[str, Any]] = []
    for row in holidays:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "").strip()
        if day not in target_dates:
            continue

        holiday_religion = _normalize_religion(str(row.get("religion") or ""))
        if holiday_religion:
            if not normalized_religion or holiday_religion != normalized_religion:
                continue
            active.append(dict(row))
            continue

        if _region_match(row.get("region"), region):
            active.append(dict(row))

    return active


def classify_day(
    date: str,
    event_count: int,
    holidays_active: list[dict[str, Any]],
    historical_avg: float,
    high_activity_threshold: float = 0.7,
) -> DayLabel:
    day = str(date).strip()
    is_holiday = any(str(item.get("date") or "").strip() == day for item in holidays_active if isinstance(item, dict))
    if historical_avg > 0:
        high = float(event_count) >= float(historical_avg) * float(high_activity_threshold)
    else:
        high = int(event_count) > 0
    if is_holiday and high:
        return "holiday_high"
    if is_holiday:
        return "holiday_low"
    if high:
        return "workday_high"
    return "workday_low"


def select_week_template(day_labels: dict[str, DayLabel]) -> WeekTemplate:
    values = list(day_labels.values())
    if sum(1 for item in values if item == "holiday_high") >= 4:
        return "still_working_in_holiday"
    if sum(1 for item in values if item == "holiday_low") >= 4:
        return "holiday_rest"
    if sum(1 for item in values if item == "workday_low") >= 4:
        return "slow_rhythm"
    return "standard"


def read_marked_holiday(week_str: str, path: Path | None = None) -> dict[str, Any] | None:
    marked_path = path or (Path.home() / ".keypulse" / "marked-holidays.json")
    if not marked_path.exists():
        return None
    try:
        payload = json.loads(marked_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if isinstance(payload, dict):
        row = payload.get(week_str)
        return dict(row) if isinstance(row, dict) else None
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("week") or "").strip() == week_str:
                return dict(item)
    return None


def _unique_holiday_names(active_holidays: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in active_holidays:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _collect_day_labels(
    week_dates: list[str],
    daily_event_counts: list[int],
    *,
    active_holidays: list[dict[str, Any]],
    historical_avg: float,
) -> dict[str, DayLabel]:
    labels: dict[str, DayLabel] = {}
    for idx, day in enumerate(week_dates):
        count = int(daily_event_counts[idx]) if idx < len(daily_event_counts) else 0
        labels[str(day)] = classify_day(
            date=str(day),
            event_count=count,
            holidays_active=active_holidays,
            historical_avg=historical_avg,
        )
    return labels


def build_holiday_context(
    week_str: str,
    week_dates: list[str],
    daily_event_counts: list[int],
    historical_avg: float,
    region: str,
    religion: str = "",
) -> HolidayContext:
    table = load_holidays_table()
    marked = read_marked_holiday(week_str)
    active_holidays = get_active_holidays(
        week_dates=week_dates,
        region=region,
        religion=religion,
        holidays_table=table,
    )
    user_marked_reason = str((marked or {}).get("reason") or "").strip() or None

    if marked:
        names = [str(item).strip() for item in ((marked.get("holiday_names") if isinstance(marked, dict) else None) or []) if str(item).strip()]
        if not names and user_marked_reason:
            names = [user_marked_reason]
        if not names:
            names = ["用户标注假期"]
        synthetic = [{"name": name, "date": str(day)} for day in week_dates for name in names]
        day_labels = _collect_day_labels(
            week_dates=week_dates,
            daily_event_counts=daily_event_counts,
            active_holidays=synthetic,
            historical_avg=historical_avg,
        )
        template = select_week_template(day_labels)
        return HolidayContext(
            week_str=week_str,
            day_labels=day_labels,
            template=template,
            holiday_names=names,
            user_marked_reason=user_marked_reason,
            is_low_activity=is_low_activity_week(daily_event_counts, historical_avg),
        )

    day_labels = _collect_day_labels(
        week_dates=week_dates,
        daily_event_counts=daily_event_counts,
        active_holidays=active_holidays,
        historical_avg=historical_avg,
    )
    template = select_week_template(day_labels)
    holiday_names = _unique_holiday_names(active_holidays)
    low_activity = is_low_activity_week(daily_event_counts, historical_avg)
    if low_activity and not holiday_names and template == "standard":
        template = "slow_rhythm"
    if low_activity and not holiday_names and template == "slow_rhythm":
        holiday_names = ["疑似周期性假期"]

    return HolidayContext(
        week_str=week_str,
        day_labels=day_labels,
        template=template,
        holiday_names=holiday_names,
        user_marked_reason=user_marked_reason,
        is_low_activity=low_activity,
    )
