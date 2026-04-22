from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keypulse.config import Config
from keypulse.health.report import write_health_report
from keypulse.store.db import get_conn, init_db


HEALTH_SCHEMA_VERSION = 1
HEALTH_JSON_PATH = Path.home() / ".keypulse" / "health.json"
DAEMON_LABEL = "com.keypulse.daemon"
LOGGER = logging.getLogger(__name__)


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
    for field_name in ("window", "clipboard", "keyboard_chunk", "idle", "manual", "browser", "ax_text", "ocr"):
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
        WHERE source IN ('keyboard_chunk', 'clipboard', 'manual', 'browser')
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
    init_db(cfg.db_path_expanded)
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

    age_sec_since_last_event = None
    if last_event_at is not None:
        age_sec_since_last_event = int((checked_at - last_event_at).total_seconds())
        if alive and age_sec_since_last_event > 600 and idle_recent == 0 and pid is not None:
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

    result = {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "checked_at": checked_at.isoformat(),
        "overall": "ok" if not alerts else "alert",
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
        "alerts": alerts,
    }
    write_health_report(HEALTH_JSON_PATH, result)
    return result
