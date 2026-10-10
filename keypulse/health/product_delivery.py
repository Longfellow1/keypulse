from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from keypulse.capabilities.builtin._common import _watchers_payload
from keypulse.health.alerts import ProductAlert, render_alert
from keypulse.observability.watcher_tiers import WATCHER_TIERS
from keypulse.store.db import get_conn

_CORE_LABELS = {
    "keyboard_chunk": "keyboard",
    "clipboard": "clipboard",
}


@dataclass(frozen=True)
class DeliveryHealth:
    alerts: list[ProductAlert]
    watcher_counts: dict[str, int]
    degraded: bool


def _emit_count_since(*, source: str, cutoff_iso: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_events WHERE source=? AND ts_start>=?",
        (source, cutoff_iso),
    ).fetchone()
    if row is None:
        return 0
    return int(row["c"] or 0)


def _watcher_failure_detail(entry: dict[str, Any] | None) -> str | None:
    """Return failure detail string if watcher is genuinely failed, else None.

    Heartbeat-based — does NOT use emit count. emit==0 is normal when the user
    isn't producing events (idle never fires, browser not used, clipboard idle).
    A None entry means the watcher isn't registered with the daemon (e.g.
    ocr_text retired, camera/browser_history not enabled) — that is NOT a
    failure, just absence.
    """
    if entry is None:
        return None
    if entry.get("heartbeat_gave_up") or entry.get("gave_up"):
        return "watcher 已放弃自愈"
    last_error = entry.get("last_error")
    if last_error:
        return f"watcher 报错：{str(last_error)[:120]}"
    if not entry.get("running"):
        return "watcher 线程未运行"
    beat_age = entry.get("last_beat_age_sec")
    timeout = entry.get("heartbeat_timeout_sec")
    if (
        isinstance(beat_age, (int, float))
        and isinstance(timeout, (int, float))
        and timeout > 0
        and beat_age > timeout * 2
    ):
        return f"心跳已 {int(beat_age)}s 未刷新（阈值 {int(timeout)}s）"
    return None


def _watcher_alerts(
    *,
    now_utc: datetime,
    window_hours: int,
) -> tuple[list[ProductAlert], dict[str, int]]:
    cutoff = (now_utc - timedelta(hours=window_hours)).isoformat()
    watchers = _watchers_payload()
    alerts: list[ProductAlert] = []
    counts: dict[str, int] = {}
    for source, tier in WATCHER_TIERS.items():
        counts[source] = _emit_count_since(source=source, cutoff_iso=cutoff)
        detail = _watcher_failure_detail(watchers.get(source))
        if detail is None:
            continue
        if tier == "core":
            label = _CORE_LABELS.get(source, source)
            alerts.append(
                render_alert(
                    level="critical",
                    source=f"watcher:{source}",
                    message=f"核心数据源故障：{label} — {detail}",
                    suggested_action="先点 HUD 重启；若仍失败，请在系统设置里检查键盘监听与辅助功能权限",
                )
            )
            continue
        if tier == "standard":
            alerts.append(
                render_alert(
                    level="warn",
                    source=f"watcher:{source}",
                    message=f"标准数据源故障：{source} — {detail}",
                    suggested_action="可先继续使用；若持续出现可点 HUD 重启做自愈",
                )
            )
            continue
        alerts.append(
            render_alert(
                level="info",
                source=f"watcher:{source}",
                message=f"可选数据源故障：{source} — {detail}",
                suggested_action="无需立即处理，仅影响补充信息完整度",
            )
        )
    return alerts, counts


def evaluate_delivery_health(
    *,
    now_utc: datetime | None = None,
    watcher_window_hours: int = 24,
) -> DeliveryHealth:
    utc_now = now_utc or datetime.now(timezone.utc)

    alerts: list[ProductAlert] = []
    watcher_alerts, counts = _watcher_alerts(now_utc=utc_now, window_hours=watcher_window_hours)
    alerts.extend(watcher_alerts)
    degraded = any(item.level in {"warn", "critical"} for item in alerts)
    return DeliveryHealth(
        alerts=alerts,
        watcher_counts=counts,
        degraded=degraded,
    )
