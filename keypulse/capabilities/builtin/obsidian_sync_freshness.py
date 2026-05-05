from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts
from keypulse.config import Config


# Tuning constants. These are the "this is normal" envelope; outside of it,
# we flag the user. Tuned to launchd schedule (hourly sync) + typical
# overnight idle (no events for 8h is fine).
_CAPTURE_RECENT_MIN = 30   # within this, capture pipeline is "active"
_SYNC_FAIL_WINDOW_MIN = 60  # if a failure happened in last hour, surface it
_SYNC_STALE_HOURS = 6       # quiet for this long while capture active = bad


class ObsidianSyncFreshnessCapability(Capability):
    """Watch for Obsidian sync that has gone silent or repeatedly failed.

    Reads `llm_trigger_log` (the table where T1/T2/T3 sync outcomes get
    written). The capability is designed to be quiet when the user is idle
    (no captures → no sync expected) and loud when capture is active but
    the last sync attempt failed or hasn't run in too long.
    """

    name = "obsidian_sync_freshness"
    level_when_failed = "warn"
    label_when_failed = "同步异常"
    error_codes = {"sync_recent_failure", "sync_stale_while_active"}

    _HINTS = {
        "sync_recent_failure": "最近一次 Obsidian 同步失败，请查看 ~/.keypulse/logs 或手动跑 `keypulse obsidian sync`",
        "sync_stale_while_active": "采集仍在进行但 Obsidian 已数小时未同步，建议手动跑 `keypulse obsidian sync`",
    }

    def _last_trigger_row(self, db_path: str) -> tuple[str, str] | None:
        """Return (ts_utc, outcome) of the most recent trigger row, or None."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ts_utc, outcome FROM llm_trigger_log
                WHERE outcome != 'pending'
                ORDER BY ts_utc DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            conn.close()
        except sqlite3.OperationalError:
            # Table may not exist yet on a fresh install.
            return None
        except Exception:
            return None
        if not row:
            return None
        return (str(row[0]), str(row[1]))

    def _last_flush_dt(self) -> datetime | None:
        from keypulse.store.repository import get_state

        raw = str(get_state("last_flush") or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _probe(self) -> CheckResult:
        cfg = Config.load()
        last_row = self._last_trigger_row(str(cfg.db_path_expanded))
        if last_row is None:
            # No trigger history yet — fresh install or DB-less env. Not a failure.
            return CheckResult(ok=True, code="ok")

        ts_str, outcome = last_row
        try:
            last_ts = datetime.fromisoformat(ts_str)
        except ValueError:
            return CheckResult(ok=True, code="ok")
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        last_ts = last_ts.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)

        is_failure = outcome.startswith("ran:fail") or outcome.startswith("error:")
        recently = (now - last_ts) <= timedelta(minutes=_SYNC_FAIL_WINDOW_MIN)
        if is_failure and recently:
            return CheckResult(
                ok=False,
                code="sync_recent_failure",
                hint=self._HINTS["sync_recent_failure"],
            )

        # No recent failure. Check for staleness while capture remains active.
        capture_dt = self._last_flush_dt()
        capture_active = (
            capture_dt is not None
            and (now - capture_dt) <= timedelta(minutes=_CAPTURE_RECENT_MIN)
        )
        if capture_active and (now - last_ts) > timedelta(hours=_SYNC_STALE_HOURS):
            return CheckResult(
                ok=False,
                code="sync_stale_while_active",
                hint=self._HINTS["sync_stale_while_active"],
            )

        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="skipped")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        hint = self._HINTS.get(state.code, "Obsidian 同步异常")
        return Signal(level=self.level_when_failed, label=self.label_when_failed, hint=hint, action=None)
