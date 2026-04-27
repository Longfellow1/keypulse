from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any

from keypulse.config import Config
from keypulse.hud.state import HUDState, read_hud_state
from keypulse.obsidian.exporter import build_obsidian_bundle
from keypulse.pipeline.surface import build_surface_snapshot
from keypulse.store.db import init_db
from keypulse.store.repository import get_state, query_raw_events
from keypulse.utils.dates import local_day_bounds, resolve_local_date


MODE_LABELS = {
    "standard": "标准模式",
    "focus": "专注模式",
    "sensitive": "高敏模式",
    "review": "回顾模式",
}

SOURCE_LABELS = {
    "manual": "手动保存",
    "clipboard": "剪贴板",
    "window": "窗口活动",
    "ax_text": "当前看到的正文",
    "ocr_text": "屏幕识别补充",
    "keyboard_chunk": "键入整理片段",
    "browser_tab": "浏览器标签页",
}

HEALTH_LABELS = {
    "ax_text": "当前看到的正文",
    "ocr": "屏幕识别补充",
    "keyboard_chunk": "键入整理片段",
}

REASON_LABELS = {
    "explicitness": "明确表达",
    "novelty": "新信息",
    "reusability": "可复用",
    "decision_signal": "有判断或决策信号",
    "density": "信息密度高",
    "recurrence": "重复出现，值得关注",
}


@dataclass(frozen=True)
class HUDSnapshot:
    date: str
    mode: str
    mode_label: str
    today_focus: str
    attention_items: list[str]
    summary_line: str
    active_sources: dict[str, bool]
    effective_count: int
    filtered_count: int
    theme_count: int
    manual_marked_count: int
    effective_count_delta_vs_yesterday: int | None
    filtered_count_delta_vs_yesterday: int | None
    theme_count_delta_vs_yesterday: int | None
    manual_marked_count_delta_vs_yesterday: int | None
    last_sync_at: str
    source_counts: dict[str, int]
    top_signals: list[dict[str, Any]]


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[\s,，。；;:：/|]+", text.lower()) if len(token) >= 2]


def _boost_score(item: dict[str, Any], today_focus: str, attention_items: list[str]) -> float:
    text = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("evidence") or ""),
            str(item.get("topic_key") or ""),
            " ".join(str(tag) for tag in item.get("tags") or []),
        ]
    ).lower()
    boost = 0.0
    for token in _tokenize(today_focus):
        if token and token in text:
            boost += 0.18
    for token in attention_items:
        if token.lower() in text:
            boost += 0.12
    return boost


def _build_top_signals(events: list[dict[str, Any]], *, today_focus: str, attention_items: list[str], vault_name: str, date_str: str) -> list[dict[str, Any]]:
    from keypulse.pipeline.surface import build_surface_snapshot

    snapshot = build_surface_snapshot(events, top_k=20)
    bundle = build_obsidian_bundle(events, vault_name=vault_name, date_str=date_str)
    path_by_title = {
        note["body"].splitlines()[0].removeprefix("# ").strip(): note["path"]
        for note in bundle.get("events", [])
    }
    candidates: list[dict[str, Any]] = []
    for item in snapshot.get("candidates", []):
        adjusted_score = round(float(item["score"]) + _boost_score(item, today_focus, attention_items), 4)
        why = [
            REASON_LABELS.get(str(reason), str(reason))
            for reason, value in dict(item.get("why_selected") or {}).items()
            if float(value or 0) > 0
        ]
        candidates.append(
            {
                "title": item["title"],
                "source": SOURCE_LABELS.get(str(item.get("source") or ""), str(item.get("source") or "未知来源")),
                "source_key": str(item.get("source") or ""),
                "reason": "、".join(why[:3]) or "被系统识别为高价值候选",
                "score": adjusted_score,
                "path": path_by_title.get(item["title"], f"Daily/{date_str}.md"),
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), item["title"]))
    return candidates[:3], snapshot


def _summarize_metrics(events: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, int]:
    filtered_total = int(snapshot.get("filtered_total", 0))
    return {
        "effective_count": max(len(events) - filtered_total, 0),
        "filtered_count": filtered_total,
        "theme_count": len(snapshot.get("theme_candidates", [])),
        "manual_marked_count": sum(1 for event in events if str(event.get("source") or "") == "manual"),
    }


def _metric_delta(today_value: int, yesterday_value: int | None) -> int | None:
    if yesterday_value is None:
        return None
    return today_value - yesterday_value


def _previous_date(date_str: str) -> str:
    return (date_cls.fromisoformat(date_str) - timedelta(days=1)).isoformat()


def _status_symbol(capture_status: str) -> str:
    if capture_status == "paused":
        return "⏸"
    if capture_status in {"running", "active"}:
        return "●"
    return "⊘"


def build_hud_snapshot(cfg: Config, *, date_str: str | None = None, hud_state_path: str | Path | None = None) -> HUDSnapshot:
    init_db(cfg.db_path_expanded)
    effective_date = resolve_local_date(date=date_str)
    since, until = local_day_bounds(effective_date)
    hud_state = read_hud_state(hud_state_path)
    events = query_raw_events(since=since, until=until, limit=5000)
    top_signals, snapshot = _build_top_signals(
        events,
        today_focus=hud_state.today_focus.get(effective_date, ""),
        attention_items=hud_state.attention_items,
        vault_name=cfg.obsidian.vault_name,
        date_str=effective_date,
    )
    today_focus = hud_state.today_focus.get(effective_date, "")
    current_metrics = _summarize_metrics(events, snapshot)
    yesterday_metrics: dict[str, int] | None = None
    yesterday_date = _previous_date(effective_date)
    yesterday_since, yesterday_until = local_day_bounds(yesterday_date)
    yesterday_events = query_raw_events(since=yesterday_since, until=yesterday_until, limit=5000)
    if yesterday_events:
        yesterday_snapshot = build_surface_snapshot(yesterday_events, top_k=20)
        yesterday_metrics = _summarize_metrics(yesterday_events, yesterday_snapshot)
    source_counts: dict[str, int] = {}
    for event in events:
        label = SOURCE_LABELS.get(str(event.get("source") or ""), str(event.get("source") or "未知来源"))
        source_counts[label] = source_counts.get(label, 0) + 1
    top_title = top_signals[0]["title"] if top_signals else "今天没有新的高价值内容"
    if today_focus:
        summary_line = f"今天重点围绕“{today_focus}”，当前最值得看的是：{top_title}"
    else:
        summary_line = f"今天最值得看的是：{top_title}"

    active_sources = {
        HEALTH_LABELS["ax_text"]: bool(getattr(cfg.watchers, "ax_text", False)),
        HEALTH_LABELS["ocr"]: bool(getattr(cfg.watchers, "ocr", False)),
        HEALTH_LABELS["keyboard_chunk"]: bool(getattr(cfg.watchers, "keyboard_chunk", False)),
    }
    return HUDSnapshot(
        date=effective_date,
        mode=hud_state.mode,
        mode_label=MODE_LABELS.get(hud_state.mode, hud_state.mode),
        today_focus=today_focus,
        attention_items=list(hud_state.attention_items),
        summary_line=summary_line,
        active_sources=active_sources,
        effective_count=current_metrics["effective_count"],
        filtered_count=current_metrics["filtered_count"],
        theme_count=current_metrics["theme_count"],
        manual_marked_count=current_metrics["manual_marked_count"],
        effective_count_delta_vs_yesterday=_metric_delta(
            current_metrics["effective_count"], yesterday_metrics["effective_count"] if yesterday_metrics else None
        ),
        filtered_count_delta_vs_yesterday=_metric_delta(
            current_metrics["filtered_count"], yesterday_metrics["filtered_count"] if yesterday_metrics else None
        ),
        theme_count_delta_vs_yesterday=_metric_delta(
            current_metrics["theme_count"], yesterday_metrics["theme_count"] if yesterday_metrics else None
        ),
        manual_marked_count_delta_vs_yesterday=_metric_delta(
            current_metrics["manual_marked_count"], yesterday_metrics["manual_marked_count"] if yesterday_metrics else None
        ),
        last_sync_at=get_state("last_flush") or "—",
        source_counts=source_counts,
        top_signals=top_signals,
    )
