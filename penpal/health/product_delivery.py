from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from penpal.health.alerts import ProductAlert, render_alert
from penpal.observability.watcher_tiers import WATCHER_TIERS
from penpal.runtime import RuntimeStatus, evaluate
from penpal.store.db import get_conn

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


def _watcher_failure_detail(entry: Any | None) -> str | None:
    """Return the daemon-record component failure, without treating silence as a fault."""
    if entry is None:
        return None
    if entry.state == "gave_up":
        return entry.detail or "watcher 已放弃重试"
    if entry.state == "missing_permission":
        return "缺少系统权限"
    if entry.state == "storage_error":
        return "采集存储错误"
    return None


def _watcher_alerts(
    *,
    now_utc: datetime,
    window_hours: int,
    runtime_status: RuntimeStatus | None = None,
) -> tuple[list[ProductAlert], dict[str, int]]:
    cutoff = (now_utc - timedelta(hours=window_hours)).isoformat()
    watchers = (runtime_status or evaluate()).components
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
    runtime_status: RuntimeStatus | None = None,
) -> DeliveryHealth:
    utc_now = now_utc or datetime.now(timezone.utc)

    alerts: list[ProductAlert] = []
    watcher_alerts, counts = _watcher_alerts(
        now_utc=utc_now,
        window_hours=watcher_window_hours,
        runtime_status=runtime_status,
    )
    alerts.extend(watcher_alerts)
    degraded = any(item.level in {"warn", "critical"} for item in alerts)
    return DeliveryHealth(
        alerts=alerts,
        watcher_counts=counts,
        degraded=degraded,
    )
