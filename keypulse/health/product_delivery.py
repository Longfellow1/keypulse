from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keypulse.health.alerts import ProductAlert, render_alert
from keypulse.observability.watcher_tiers import WATCHER_TIERS
from keypulse.store.db import get_conn
from keypulse.utils.dates import resolve_local_date
from keypulse.utils.paths import get_data_dir

_CORE_LABELS = {
    "keyboard_chunk": "keyboard",
    "clipboard": "clipboard",
}


@dataclass(frozen=True)
class DeliveryHealth:
    alerts: list[ProductAlert]
    run_record_ok: bool
    run_record_reason: str
    watcher_counts: dict[str, int]
    degraded: bool


def _record_path(date_str: str, records_dir: Path | None = None) -> Path:
    root = records_dir or (get_data_dir() / "run_records")
    return root / f"{date_str}.json"


def _load_run_record(date_str: str, records_dir: Path | None = None) -> dict[str, Any] | None:
    path = _record_path(date_str, records_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _run_record_alert(
    now_local: datetime,
    *,
    records_dir: Path | None = None,
) -> tuple[ProductAlert | None, bool, str]:
    if (now_local.hour, now_local.minute) < (18, 30):
        return (None, True, "before_cutoff")
    today = resolve_local_date(now=now_local)
    payload = _load_run_record(today, records_dir=records_dir)
    if payload is None:
        return (
            render_alert(
                level="critical",
                source="daily",
                message="今日日报未生成",
                suggested_action="点击 HUD 重启，让系统自动补跑今日日报",
            ),
            False,
            "missing",
        )
    stage_status = payload.get("stage_status")
    if not isinstance(stage_status, dict):
        return (None, True, "no_stage_status")
    degraded = [name for name, status in stage_status.items() if str(status) != "ok"]
    if degraded:
        degraded_reason = str(payload.get("degraded_reason") or "").strip()
        reason = degraded_reason or ",".join(degraded[:3])
        return (
            render_alert(
                level="warn",
                source="daily",
                message=f"日报降级：{reason}",
                suggested_action="点击 HUD 重启，系统会尝试自愈并在必要时补跑日报",
            ),
            False,
            reason,
        )
    return (None, True, "")


def _emit_count_since(*, source: str, cutoff_iso: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_events WHERE source=? AND ts_start>=?",
        (source, cutoff_iso),
    ).fetchone()
    if row is None:
        return 0
    return int(row["c"] or 0)


def _watcher_alerts(
    *,
    now_utc: datetime,
    window_hours: int,
) -> tuple[list[ProductAlert], dict[str, int]]:
    cutoff = (now_utc - timedelta(hours=window_hours)).isoformat()
    alerts: list[ProductAlert] = []
    counts: dict[str, int] = {}
    for source, tier in WATCHER_TIERS.items():
        count = _emit_count_since(source=source, cutoff_iso=cutoff)
        counts[source] = count
        if count > 0:
            continue
        if tier == "core":
            label = _CORE_LABELS.get(source, source)
            alerts.append(
                render_alert(
                    level="critical",
                    source=f"watcher:{source}",
                    message=f"核心数据源失活：{label} 最近 {window_hours}h 无新数据",
                    suggested_action="先点 HUD 重启；若仍失败，请在系统设置里检查键盘监听与辅助功能权限",
                )
            )
            continue
        if tier == "standard":
            alerts.append(
                render_alert(
                    level="warn",
                    source=f"watcher:{source}",
                    message=f"标准数据源失活：{source} 最近 {window_hours}h 无新数据",
                    suggested_action="可先继续使用；若持续出现可点 HUD 重启做自愈",
                )
            )
            continue
        alerts.append(
            render_alert(
                level="info",
                source=f"watcher:{source}",
                message=f"可选数据源缺失：{source} 最近 {window_hours}h 无新数据",
                suggested_action="无需立即处理，仅影响补充信息完整度",
            )
        )
    return alerts, counts


def evaluate_delivery_health(
    *,
    now_local: datetime | None = None,
    now_utc: datetime | None = None,
    records_dir: Path | None = None,
    watcher_window_hours: int = 24,
) -> DeliveryHealth:
    local_now = now_local or datetime.now().astimezone()
    utc_now = now_utc or datetime.now(timezone.utc)

    alerts: list[ProductAlert] = []
    run_alert, run_ok, run_reason = _run_record_alert(local_now, records_dir=records_dir)
    if run_alert is not None:
        alerts.append(run_alert)
    watcher_alerts, counts = _watcher_alerts(now_utc=utc_now, window_hours=watcher_window_hours)
    alerts.extend(watcher_alerts)
    degraded = any(item.level in {"warn", "critical"} for item in alerts)
    return DeliveryHealth(
        alerts=alerts,
        run_record_ok=run_ok,
        run_record_reason=run_reason,
        watcher_counts=counts,
        degraded=degraded,
    )
