from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any

from keypulse.capabilities.base import HealthState
from keypulse.capabilities.registry import get_default_registry
from keypulse.capabilities.store import load_states as load_capability_states
from keypulse.config import Config
from keypulse.hud.health import read_health
from keypulse.hud.state import HUDState, read_hud_state
from keypulse.obsidian.exporter import build_obsidian_bundle
from keypulse.pipeline.surface import build_surface_snapshot
from keypulse.store.db import init_db
from keypulse.store.repository import get_state, query_raw_events, set_state
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
    "browser_tab": "浏览器标签页",
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


_CAPTURE_CAPS = {"appkit_runtime", "accessibility_permission", "clipboard_watcher"}
_LLM_CAPS = {"llm_backend"}


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


def determine_service_status(*, capture_status: str, health_ok: bool) -> tuple[str, str, str, str]:
    """Returns (level, label, hint_message, hint_action). Level ∈ ok/warn/err/gray.

    hint_action carries the Capability.diagnose() action URL when available
    (e.g. "open://x-apple.systempreferences:..."), so the HUD can render a
    1-click "打开设置" button in the hint bar. Empty string when no action.

    Priority (first hit wins):
      1. paused              → gray
      2. capture_error_code  → err  (specific hint per code)
      3. health stale        → warn (体检员失联，但采集本身可能还活着)
      4. llm_error_code      → warn
      5. ok                  → ok
    """
    if capture_status == "paused":
        return ("gray", "已暂停", "", "")

    capability_states = load_capability_states()
    if not capability_states:
        capability_states = _capability_states_from_health(read_health())
    if capability_states:
        signal = get_default_registry().aggregate_signal(capability_states)
        return (signal.level, signal.label, signal.hint or "", signal.action or "")

    capture_code = (get_state("capture_error_code") or "").strip()
    if capture_code:
        return _legacy_signal(
            capture_code,
            names=_CAPTURE_CAPS,
            fallback_label="采集异常",
            fallback_hint="采集组件异常，请重启 daemon",
        )

    if not health_ok:
        return ("warn", "体检失联", "健康监测未在运行，状态可能不准；请运行 make install 重挂体检", "")

    llm_code = (get_state("llm_error_code") or "").strip()
    if llm_code:
        return _legacy_signal(
            llm_code,
            names=_LLM_CAPS,
            fallback_label="LLM 异常",
            fallback_hint="LLM 调用异常，请稍后重试",
        )

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


def _obsidian_open_url(vault_root: str, note_path: str, *, heading: str | None = None) -> str:
    """obsidian://open?vault=<basename>&file=<path>[#heading]。

    vault 名取 vault_root 路径的 basename —— 这是 Obsidian 实际注册的 vault
    标识。带 heading 时跳到该 H3 锚点（Obsidian URI 支持 file=path#heading）。
    """
    from pathlib import Path
    from urllib.parse import quote

    vault_id = Path(vault_root).name or vault_root
    file_part = note_path[:-3] if note_path.endswith(".md") else note_path
    if heading:
        file_part = f"{file_part}#{heading}"
    return f"obsidian://open?vault={quote(vault_id, safe='')}&file={quote(file_part, safe='/')}"


_DAILY_GENERIC_TOPICS = {"碎片汇总", "其它", "其他", "杂项"}


def _parse_daily_topics(daily_body: str) -> list[tuple[str, str]]:
    """从 daily.md 的「## 今日主线」段解析 H3 主题。

    返回 [(主题名, 原始 H3 文本), ...]，按 daily 中出现顺序（早→晚）。
    H3 格式：`### 凌晨访问pairdrop网站 · 5m（2026年5月5日 22:48–22:53）`
    主题名 = ` · ` 之前那段；锚点 = 整个 H3 文本。过滤"碎片汇总"等泛词。
    """
    if not daily_body:
        return []
    lines = daily_body.splitlines()
    main_section_start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("## 今日主线"):
            main_section_start = idx
            break
    if main_section_start is None:
        return []
    out: list[tuple[str, str]] = []
    for line in lines[main_section_start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            break
        if stripped.startswith("### "):
            heading = stripped[4:].strip()
            topic = heading.split(" · ", 1)[0].strip()
            if topic and topic not in _DAILY_GENERIC_TOPICS:
                out.append((topic, heading))
    return out


def _build_top_signals(
    events: list[dict[str, Any]],
    *,
    today_focus: str,
    attention_items: list[str],
    vault_name: str,
    vault_root: str,
    date_str: str,
) -> list[dict[str, Any]]:
    """HUD「今天最新」三条 = 日报「今日主线」的主题 H3。

    跳转走 obsidian:// + heading 锚点，直接落到 daily 那一段。
    主题不足 3 条时由 HUD 渲染层兜底（占位 / 兜底句）。
    """
    from keypulse.pipeline.surface import build_surface_snapshot

    snapshot = build_surface_snapshot(events, top_k=20)
    bundle = build_obsidian_bundle(events, vault_name=vault_name, date_str=date_str)
    daily_cards = list(bundle.get("daily") or [])
    daily_body = str(daily_cards[0].get("body") or "") if daily_cards else ""
    daily_path = (
        str(daily_cards[0].get("path") or f"Daily/{date_str}.md")
        if daily_cards
        else f"Daily/{date_str}.md"
    )

    topics = _parse_daily_topics(daily_body)
    # 取最新 3 条 = 出现位置末尾 3 条（daily 内主题按时间从早到晚排）
    latest = list(reversed(topics[-3:]))

    candidates: list[dict[str, Any]] = []
    for topic_name, heading in latest:
        candidates.append(
            {
                "title": topic_name,
                "source": "今日主线",
                "source_key": "daily_topic",
                "reason": "新信息",
                "score": 1.0,
                "path": daily_path,
                "topic_key": topic_name,
                "obsidian_url": _obsidian_open_url(vault_root, daily_path, heading=heading),
            }
        )
    return candidates, snapshot


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


def build_hud_snapshot(
    cfg: Config,
    *,
    date_str: str | None = None,
    hud_state_path: str | Path | None = None,
    capture_status: str = "running",
    health_ok: bool = True,
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
        vault_name=cfg.obsidian.vault_name,
        vault_root=str(Path(cfg.obsidian.vault_path).expanduser()),
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
    }
    service_level, status_label, hint_message, hint_action = determine_service_status(
        capture_status=capture_status, health_ok=health_ok
    )
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
