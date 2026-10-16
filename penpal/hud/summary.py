from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

from penpal.app import _CAPTURE_FACT_CAPS, _CAPTURE_PROBE_CAPS
from penpal.capabilities.base import HealthState
from penpal.capabilities.registry import get_default_registry
from penpal.capabilities.store import load_states as load_capability_states
from penpal.config import Config
from penpal.health.product_delivery import evaluate_delivery_health
from penpal.hud.health import read_health
from penpal.hud.state import read_hud_state
from penpal.hud.surface import build_surface_snapshot
from penpal.store.db import init_db
from penpal.store.repository import get_state, query_raw_events, set_state
from penpal.utils.dates import local_day_bounds, resolve_local_date


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
    "browser_tab": "浏览器标签页",
    "browser_url": "浏览器 URL",
}

HEALTH_LABELS = {
    "ax_text": "当前看到的正文",
    "ocr": "屏幕识别补充",
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
    service_status: str = "ok"
    status_label: str = "正常"
    hint_message: str = ""
    hint_action: str = ""
    companion_days: int = 1


def _companion_days(today_iso: str) -> int:
    install_date_str = get_state("install_date")
    if not install_date_str:
        set_state("install_date", today_iso)
        return 1
    try:
        install_date = date_cls.fromisoformat(install_date_str)
        today = date_cls.fromisoformat(today_iso)
    except ValueError:
        return 1
    return max((today - install_date).days + 1, 1)


_LEGACY_CAPTURE_SIGNAL_CAPS = _CAPTURE_FACT_CAPS | _CAPTURE_PROBE_CAPS


def _capability_states_from_health(payload: dict[str, Any] | None) -> dict[str, HealthState]:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("capabilities")
    if not isinstance(raw, dict):
        return {}
    states: dict[str, HealthState] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        try:
            state = HealthState(
                ok=bool(value.get("ok")),
                code=str(value.get("code") or ""),
                last_checked=float(value.get("last_checked") or 0.0),
                detail=str(value.get("detail")) if value.get("detail") is not None else None,
            )
        except (TypeError, ValueError):
            continue
        states[name] = state
    return states


def _legacy_signal(code: str, *, names: set[str], fallback_label: str, fallback_hint: str) -> tuple[str, str, str, str]:
    registry = get_default_registry()
    cap = registry.find_capability_for_code(code, names=names)
    if cap is None:
        return ("warn", fallback_label, fallback_hint, "")
    signal = cap.diagnose(HealthState(ok=False, code=code, last_checked=time.time()))
    return (signal.level, signal.label, signal.hint or "", signal.action or "")


def _probe_hint(label: str, hint: str, action: str) -> tuple[str, str, str, str]:
    return ("warn", "采集建议", hint or label, action)


def determine_service_status(*, capture_status: str) -> tuple[str, str, str, str]:
    """Returns (level, label, hint_message, hint_action). Level ∈ ok/warn/err/gray.

    hint_action carries the Capability.diagnose() action URL when available
    (e.g. "open://x-apple.systempreferences:..."), so the HUD can render a
    1-click "打开设置" button in the hint bar. Empty string when no action.

    Priority (first hit wins):
      1. paused                         → gray
      2. capability layer (runtime truth, 13 caps incl. health_freshness):
         2a. capture fact failure       → err
         2b. non-probe failure          → diagnose() level (warn/err)
         2c. capture probe failure      → warn (permission/runtime hint)
      3. legacy capture_error_code      → err  (灾备：capability state 完全缺失)
      4. ok                             → ok

    The health_freshness capability (step 2b) owns the "healthcheck alive"
    judgement. We deliberately do NOT consult ``health.json.overall`` here —
    it mixes runtime health with business alerts (e.g. SYNC_STALE) and
    misreports unrelated warnings as "健康监测未在运行".
    """
    if capture_status == "paused":
        return ("gray", "已暂停", "", "")

    capability_states = load_capability_states()
    if not capability_states:
        capability_states = _capability_states_from_health(read_health())
    if capability_states:
        registry = get_default_registry()
        capture_failure = registry.select_failure(capability_states, names=_CAPTURE_FACT_CAPS)
        if capture_failure is not None:
            signal = capture_failure.signal
            return ("err", signal.label, signal.hint or "", signal.action or "")

        non_probe_names = set(capability_states) - _CAPTURE_PROBE_CAPS
        non_probe_failure = registry.select_failure(capability_states, names=non_probe_names)
        if non_probe_failure is not None:
            signal = non_probe_failure.signal
            return (signal.level, signal.label, signal.hint or "", signal.action or "")

        probe_failure = registry.select_failure(capability_states, names=_CAPTURE_PROBE_CAPS)
        if probe_failure is not None:
            signal = probe_failure.signal
            return _probe_hint(signal.label, signal.hint or "", signal.action or "")

    capture_code = (get_state("capture_error_code") or "").strip()
    if capture_code:
        _level, label, hint, action = _legacy_signal(
            capture_code,
            names=_LEGACY_CAPTURE_SIGNAL_CAPS,
            fallback_label="采集异常",
            fallback_hint="采集组件异常，请重启 daemon",
        )
        return ("err", label, hint, action)

    return ("ok", "都好", "", "")


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


def _build_top_signals(
    events: list[dict[str, Any]],
    *,
    today_focus: str,
    attention_items: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """HUD「今天最新」三条：由当天核心事件（规则打分，无 LLM / 无报告产物）派生。"""
    from penpal.hud.surface import build_surface_snapshot

    snapshot = build_surface_snapshot(events, top_k=20)
    ranked = sorted(
        snapshot.get("candidates", []),
        key=lambda item: -(float(item.get("score") or 0.0) + _boost_score(item, today_focus, attention_items)),
    )
    signals: list[dict[str, Any]] = []
    for item in ranked[:3]:
        labels = list(item.get("why_labels") or [])
        signals.append(
            {
                "title": str(item.get("title") or "").strip() or "未命名",
                "source": SOURCE_LABELS.get(str(item.get("source") or ""), str(item.get("source") or "")),
                "source_key": str(item.get("source") or ""),
                "reason": "、".join(labels) or "新信息",
                "score": float(item.get("score") or 0.0),
                "topic_key": item.get("topic_key"),
            }
        )
    return signals, snapshot


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


def _self_heal_progress() -> str:
    raw = str(get_state("self_heal_progress") or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("message") or "").strip()


def _choose_top_status(*, capture_status: str) -> tuple[str, str, str]:
    if capture_status == "paused":
        return ("gray", "已暂停", "")

    if str(get_state("self_heal_running") or "").strip() == "1":
        progress = _self_heal_progress() or "系统正在自愈，请稍候"
        return ("warn", "自愈中", progress)

    delivery = evaluate_delivery_health(watcher_window_hours=1)
    critical = [item for item in delivery.alerts if item.level == "critical"]
    if critical:
        return ("err", "需要处理", critical[0].message)
    warn = [item for item in delivery.alerts if item.level == "warn"]
    if warn:
        return ("warn", "轻度降级", warn[0].message)
    return ("ok", "今天已就绪", "")


def build_hud_snapshot(
    cfg: Config,
    *,
    date_str: str | None = None,
    hud_state_path: str | Path | None = None,
    capture_status: str = "running",
) -> HUDSnapshot:
    init_db(cfg.db_path_expanded)
    effective_date = resolve_local_date(date=date_str)
    since, until = local_day_bounds(effective_date)
    hud_state = read_hud_state(hud_state_path)
    events = query_raw_events(since=since, until=until, limit=5000)
    top_signals, snapshot = _build_top_signals(
        events,
        today_focus=hud_state.today_focus.get(effective_date, ""),
        attention_items=hud_state.attention_items,
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
    }
    service_level, status_label, hint_message = _choose_top_status(capture_status=capture_status)
    hint_action = ""
    cap_level, _cap_label, cap_hint, cap_action = determine_service_status(capture_status=capture_status)
    if service_level == "ok" and cap_level in {"warn", "err"}:
        hint_message = cap_hint or "采集能力有轻微异常，不影响今日交付"
        hint_action = cap_action
    companion_days = _companion_days(effective_date)
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
        service_status=service_level,
        status_label=status_label,
        hint_message=hint_message,
        hint_action=hint_action,
        companion_days=companion_days,
    )
