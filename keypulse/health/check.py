from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keypulse.capabilities.registry import get_default_registry
from keypulse.capabilities.store import load_states, load_states_raw
from keypulse.config import Config
from keypulse.health.alerts import ProductAlert, write_alerts
from keypulse.health.product_delivery import evaluate_delivery_health
from keypulse.health.report import HEALTH_JSON_PATH, write_health_report
from keypulse.store.repository import get_state, set_state
from keypulse.store.db import get_conn, init_db


HEALTH_SCHEMA_VERSION = 1
DAEMON_LABEL = "com.keypulse.daemon"
LOGGER = logging.getLogger(__name__)
_HEALTH_DEGRADED_STREAK_KEY = "healthcheck_degraded_streak"
_HEALTH_LAST_DEGRADED_AT_KEY = "healthcheck_last_degraded_at"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_config_from_path(path: Path) -> Config:
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def _load_config(config_path: str | None) -> tuple[Config, bool]:
    try:
        if config_path:
            return _load_config_from_path(Path(config_path)), True
        return Config.load(), True
    except Exception:
        return Config(), False


def _launchctl_pid() -> int | None:
    try:
        result = subprocess.run(
            ["launchctl", "list", DAEMON_LABEL],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    output = "\n".join(filter(None, [result.stdout or "", result.stderr or ""]))
    match = re.search(r'"?PID"?\s*=\s*(\d+)', output)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    return True


def _datetime_from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        if value.endswith("Z"):
            try:
                return datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError:
                return None
        return None


def _json_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _watcher_names(cfg: Config) -> list[str]:
    names: list[str] = []
    for field_name in ("window", "clipboard", "idle", "manual", "browser", "ax_text", "ocr"):
        if bool(getattr(cfg.watchers, field_name, False)):
            names.append(field_name)
    return names


def _latest_daily_sync(vault_path: str, checked_at: datetime) -> tuple[str | None, str | None, bool]:
    daily_dir = Path(vault_path).expanduser() / "Daily"
    if not daily_dir.exists():
        return None, None, True

    files = [path for path in daily_dir.rglob("*") if path.is_file()]
    if not files:
        return None, None, True

    latest = max(files, key=lambda path: path.stat().st_mtime)
    mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", latest.name)
    age_sec = (checked_at - mtime).total_seconds()
    is_stale = age_sec > 3 * 3600
    return _json_iso(mtime), date_match.group(0) if date_match else None, is_stale


def _speaker_mislabel_ratio(checked_at: datetime) -> float:
    conn = get_conn()
    cutoff = (checked_at - timedelta(hours=1)).isoformat()
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN speaker='system' THEN 1 ELSE 0 END) AS system_count
        FROM raw_events
        WHERE source IN ('clipboard', 'manual', 'browser')
          AND ts_start > ?
        """,
        (cutoff,),
    ).fetchone()
    total = int(row["total"] or 0)
    if total == 0:
        return 0.0
    system_count = int(row["system_count"] or 0)
    return system_count / total


def run_healthcheck(config_path: str | None = None) -> dict[str, Any]:
    checked_at = _utc_now()
    cfg, config_loadable = _load_config(config_path)
    db_path = cfg.db_path_expanded if config_loadable else Path("/tmp/keypulse-healthcheck.db")
    init_db(db_path)
    conn = get_conn()

    alerts: list[dict[str, str]] = []

    pid = _launchctl_pid()
    alive = _process_alive(pid)
    if not alive:
        alerts.append(
            {
                "severity": "error",
                "code": "DAEMON_DEAD",
                "message": "daemon 进程不存在",
            }
        )

    last_event_row = conn.execute("SELECT MAX(ts_start) AS last_event_at FROM raw_events").fetchone()
    last_event_at = _datetime_from_iso(last_event_row["last_event_at"] if last_event_row else None)
    cutoff_10min = (checked_at - timedelta(minutes=10)).isoformat()
    events_last_10min_row = conn.execute(
        "SELECT COUNT(*) AS count FROM raw_events WHERE ts_start > ?",
        (cutoff_10min,),
    ).fetchone()
    events_last_10min = int(events_last_10min_row["count"] or 0)
    idle_recent_row = conn.execute(
        "SELECT COUNT(*) AS count FROM raw_events WHERE event_type='idle_start' AND ts_start > ?",
        (cutoff_10min,),
    ).fetchone()
    idle_recent = int(idle_recent_row["count"] or 0)

    last_idle_row = conn.execute(
        "SELECT event_type FROM raw_events WHERE event_type IN ('idle_start','idle_end') ORDER BY ts_start DESC LIMIT 1"
    ).fetchone()
    currently_idle = bool(last_idle_row and last_idle_row["event_type"] == "idle_start")

    age_sec_since_last_event = None
    if last_event_at is not None:
        age_sec_since_last_event = int((checked_at - last_event_at).total_seconds())
        stream_quiet = (
            alive
            and age_sec_since_last_event > 600
            and idle_recent == 0
            and not currently_idle
            and pid is not None
        )
        if stream_quiet:
            alerts.append(
                {
                    "severity": "warn",
                    "code": "STALE_EVENT_STREAM",
                    "message": "最近 10 分钟没有事件流进来，疑似 daemon 假死",
                }
            )
            try:
                os.kill(pid, 9)
                LOGGER.warning("Healthcheck killed stale daemon pid=%s", pid)
            except Exception:
                LOGGER.exception("Failed to kill stale daemon pid=%s", pid)

    schema_row = conn.execute("SELECT MAX(version) AS version FROM _schema_version").fetchone()
    schema_version = int(schema_row["version"] or 0) if schema_row else 0

    watcher_names = _watcher_names(cfg)
    if not watcher_names:
        alerts.append(
            {
                "severity": "error",
                "code": "CRITICAL_WATCHERS_DISABLED",
                "message": "关键 watcher 全部关闭，巡检与采集都会失去覆盖面",
            }
        )

    ratio = _speaker_mislabel_ratio(checked_at)
    if ratio > 0.10:
        alerts.append(
            {
                "severity": "warn",
                "code": "SPEAKER_MISLABEL_SPIKE",
                "message": "最近 1 小时 user-source 事件里 system speaker 占比过高，疑似 backfill/打标回归",
            }
        )

    sync_last_at, sync_last_date, sync_stale = _latest_daily_sync(cfg.obsidian.vault_path, checked_at)
    if sync_stale:
        alerts.append(
            {
                "severity": "warn",
                "code": "SYNC_STALE",
                "message": "Daily 同步文件已久未更新，或 Daily 目录暂无可用文件",
            }
        )

    if not config_loadable:
        alerts.append(
            {
                "severity": "error",
                "code": "CONFIG_LOAD_FAILED",
                "message": "配置文件无法加载，已回退到默认配置继续巡检",
            }
        )

    capabilities_snapshot, capture_error_code, llm_error_code, paused_flag = _capabilities_snapshot()
    delivery = evaluate_delivery_health()
    alerts_path = HEALTH_JSON_PATH.parent / "alerts.json"
    try:
        write_alerts(delivery.alerts, path=alerts_path)
    except Exception:
        LOGGER.exception("failed to write alerts.json")

    normalized_alerts = list(alerts)
    normalized_alerts.extend(_legacy_alerts_from_product_alerts(delivery.alerts))
    degraded_streak = _update_degraded_streak(any(item["severity"] in {"warn", "error"} for item in normalized_alerts))

    self_heal_triggered = False
    self_heal_result: dict[str, Any] = {}
    if degraded_streak >= 2:
        self_heal_result = _trigger_self_heal()
        self_heal_triggered = bool(self_heal_result.get("started"))

    result = {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "checked_at": checked_at.isoformat(),
        "overall": "ok" if not normalized_alerts else "alert",
        "daemon": {
            "alive": alive,
            "pid": pid,
            "last_event_at": _json_iso(last_event_at),
            "events_last_10min": events_last_10min,
            "age_sec_since_last_event": age_sec_since_last_event,
        },
        "integrity": {
            "schema_version": schema_version,
            "config_loadable": config_loadable,
            "key_watchers_enabled": watcher_names,
            "speaker_ratio_system_in_user_source_last_1h": ratio,
        },
        "sync": {
            "last_daily_sync_at": sync_last_at,
            "last_daily_file_date": sync_last_date,
        },
        "alerts": normalized_alerts,
        "product_status": {
            "run_record_ok": delivery.run_record_ok,
            "run_record_reason": delivery.run_record_reason,
            "watcher_emit_counts_24h": delivery.watcher_counts,
            "alerts_path": str(alerts_path),
            "degraded_streak": degraded_streak,
            "self_heal_triggered": self_heal_triggered,
        },
        "self_heal": self_heal_result,
        "capabilities": capabilities_snapshot,
        "capture_error_code": capture_error_code,
        "llm_error_code": llm_error_code,
        "paused": paused_flag,
    }
    write_health_report(HEALTH_JSON_PATH, result)
    return result


def _legacy_alerts_from_product_alerts(alerts: list[ProductAlert]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in alerts:
        if item.level == "info":
            continue
        out.append(
            {
                "severity": "error" if item.level == "critical" else "warn",
                "code": f"PRODUCT_{item.source.upper().replace(':', '_')}",
                "message": item.message,
            }
        )
    return out


def _update_degraded_streak(is_degraded: bool) -> int:
    raw = (get_state(_HEALTH_DEGRADED_STREAK_KEY) or "").strip()
    try:
        previous = int(raw)
    except ValueError:
        previous = 0
    if not is_degraded:
        set_state(_HEALTH_LAST_DEGRADED_AT_KEY, "")
        current = 0
        set_state(_HEALTH_DEGRADED_STREAK_KEY, str(current))
        return current

    last_raw = (get_state(_HEALTH_LAST_DEGRADED_AT_KEY) or "").strip()
    now = _utc_now()
    last_dt = _datetime_from_iso(last_raw)
    if previous <= 0 or last_dt is None:
        current = 1
    else:
        elapsed = (now - last_dt).total_seconds()
        current = previous + 1 if elapsed >= 600 else previous
    set_state(_HEALTH_DEGRADED_STREAK_KEY, str(current))
    set_state(_HEALTH_LAST_DEGRADED_AT_KEY, now.isoformat())
    return current


def _trigger_self_heal() -> dict[str, Any]:
    try:
        from keypulse.health.self_heal import run_self_heal
    except Exception as exc:
        return {"started": False, "reason": f"self_heal_import_failed:{type(exc).__name__}"}
    try:
        result = run_self_heal(dry_run=False, source="healthcheck")
    except Exception as exc:
        LOGGER.exception("healthcheck self-heal trigger failed")
        return {"started": False, "reason": f"self_heal_failed:{type(exc).__name__}"}
    return {
        "started": bool(result.get("started")),
        "status": str(result.get("status") or ""),
        "failed_step": str(result.get("failed_step") or ""),
    }


def _capabilities_snapshot() -> tuple[dict[str, Any], str, str, bool]:
    """Read latest capability states from the state repo and derive compat fields.

    Returns (capabilities_dict, capture_error_code, llm_error_code, paused).
    Falls back to ({}, "", "", False) on any error so health report generation
    never fails because of capability bugs.
    """
    from keypulse.store.repository import get_state

    try:
        capabilities_raw = load_states_raw()
        states = load_states()
    except Exception:
        return {}, "", "", False

    from keypulse.app import _CAPTURE_FACT_CAPS, _LLM_FACT_CAPS

    registry = get_default_registry()
    capture_failure = registry.select_failure(states, names=_CAPTURE_FACT_CAPS) if states else None
    llm_failure = registry.select_failure(states, names=_LLM_FACT_CAPS) if states else None
    capture_code = "" if capture_failure is None else capture_failure.state.code
    llm_code = "" if llm_failure is None else llm_failure.state.code

    try:
        paused = str(get_state("status") or "running").strip() == "paused"
    except Exception:
        paused = False

    return capabilities_raw, capture_code, llm_code, paused
