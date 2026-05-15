from __future__ import annotations

import json
import sqlite3
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.paths import get_data_dir, get_db_path

RunKind = Literal["daily", "weekly"]
StageState = Literal["ok", "degraded", "failed"]
OutputQuality = Literal["ok", "degraded", "empty"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RunRecorder:
    """Product-facing ledger for a daily/weekly pipeline run."""

    def __init__(
        self,
        *,
        date_str: str,
        kind: RunKind,
        trigger: str,
        db_path: Path | None = None,
        records_dir: Path | None = None,
    ) -> None:
        self.run_id = str(uuid.uuid4())
        self.date = date_str
        self.kind = kind
        self.trigger = trigger
        self.db_path = db_path or get_db_path()
        self.records_dir = records_dir or (get_data_dir() / "run_records")
        self.started_at_utc = _utc_now_iso()
        self.finished_at_utc = ""
        self.input_count = 0
        self.stage_status: dict[str, StageState] = {}
        self.stage_details: dict[str, dict[str, str]] = {}
        self.output_quality: OutputQuality = "empty"
        self.degraded_reason = ""
        self.llm_error_kind = ""
        self.error_trace = ""
        self.failed_stage = ""
        self.failure_reason = ""
        self.error_class = ""
        self.artifact_paths = {
            "daily_summary_json": "",
            "obsidian_md": "",
        }
        self.artifact_checksums: dict[str, dict[str, Any]] = {}
        self.cost = {
            "in_tokens": 0,
            "out_tokens": 0,
            "cost_usd": 0.0,
        }
        self.core_watcher_emit_counts: dict[str, int] = {}

    def __enter__(self) -> "RunRecorder":
        if not self.started_at_utc:
            self.started_at_utc = _utc_now_iso()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.record_exception(exc, tb=tb)
        self.finish()
        return False

    @contextmanager
    def stage(self, name: str, *, failure_reason: str = "") -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            self.mark_stage(
                name,
                "failed",
                reason=failure_reason or f"{name}_failed",
                error_class=type(exc).__name__,
            )
            if not self.failed_stage:
                self.failed_stage = name
            if not self.failure_reason:
                self.failure_reason = failure_reason or f"{name}_failed"
            raise
        else:
            if self.stage_status.get(name) != "degraded":
                self.mark_stage(name, "ok")

    def mark_stage(
        self,
        name: str,
        status: StageState,
        *,
        reason: str = "",
        error_class: str = "",
    ) -> None:
        self.stage_status[name] = status
        if reason or error_class:
            details = dict(self.stage_details.get(name, {}))
            if reason:
                details["reason"] = reason
            if error_class:
                details["error_class"] = error_class
            self.stage_details[name] = details
        if status == "failed":
            self.failed_stage = self.failed_stage or name
            self.failure_reason = self.failure_reason or reason
            self.error_class = self.error_class or error_class
            self.output_quality = "empty"
        elif status == "degraded":
            self.output_quality = "degraded"
            if reason and not self.degraded_reason:
                self.degraded_reason = reason

    def record_exception(self, exc: BaseException, *, tb: Any = None) -> None:
        self.error_class = type(exc).__name__
        if tb is None:
            self.error_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            self.error_trace = "".join(traceback.format_exception(type(exc), exc, tb))
        if not self.failed_stage:
            self.failed_stage = "unknown"
            self.stage_status[self.failed_stage] = "failed"
            self.stage_details[self.failed_stage] = {
                "reason": "unhandled_exception",
                "error_class": self.error_class,
            }
        if not self.failure_reason:
            self.failure_reason = self.stage_details.get(self.failed_stage, {}).get("reason", "unhandled_exception")
        self.output_quality = "empty"

    def set_input_count(self, count: int) -> None:
        self.input_count = max(int(count or 0), 0)

    def set_output_quality(self, quality: OutputQuality, *, degraded_reason: str = "") -> None:
        self.output_quality = quality
        if quality == "degraded":
            self.degraded_reason = degraded_reason or self.degraded_reason
        elif quality == "ok":
            self.degraded_reason = ""
            self.llm_error_kind = ""

    def set_degraded(self, reason: str, *, kind: str = "") -> None:
        self.set_output_quality("degraded", degraded_reason=reason)
        if kind:
            self.llm_error_kind = str(kind).strip()

    def set_artifact_paths(self, *, daily_summary_json: str = "", obsidian_md: str = "") -> None:
        if daily_summary_json:
            self.artifact_paths["daily_summary_json"] = daily_summary_json
        if obsidian_md:
            self.artifact_paths["obsidian_md"] = obsidian_md

    def set_artifact_checksum(
        self,
        *,
        artifact_key: str,
        stage: str,
        path: str,
        sha256: str,
        content_length: int,
        verified: bool,
    ) -> None:
        key = str(artifact_key or "").strip()
        if not key:
            return
        self.artifact_checksums[key] = {
            "stage": str(stage or "").strip(),
            "path": str(path or "").strip(),
            "sha256": str(sha256 or "").strip(),
            "content_length": max(int(content_length or 0), 0),
            "verified": bool(verified),
        }

    def set_cost(self, cost: Mapping[str, Any]) -> None:
        self.cost = {
            "in_tokens": int(cost.get("in_tokens") or 0),
            "out_tokens": int(cost.get("out_tokens") or 0),
            "cost_usd": float(cost.get("cost_usd") or 0.0),
        }

    def set_core_watcher_emit_counts(self, counts: Mapping[str, Any]) -> None:
        self.core_watcher_emit_counts = {str(key): int(value or 0) for key, value in counts.items()}

    def finish(self) -> None:
        self.finished_at_utc = self.finished_at_utc or _utc_now_iso()
        if self.output_quality == "empty" and not self.error_trace:
            self.output_quality = "ok"
        self.write()

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "date": self.date,
            "kind": self.kind,
            "trigger": self.trigger,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "input_count": self.input_count,
            "stage_status": dict(self.stage_status),
            "stage_details": dict(self.stage_details),
            "output_quality": self.output_quality,
            "degraded_reason": self.degraded_reason,
            "llm_error_kind": self.llm_error_kind,
            "failed_stage": self.failed_stage,
            "failure_reason": self.failure_reason,
            "error_class": self.error_class,
            "error_trace": self.error_trace,
            "artifact_paths": dict(self.artifact_paths),
            "artifact_checksums": dict(self.artifact_checksums),
            "cost": dict(self.cost),
            "core_watcher_emit_counts": dict(self.core_watcher_emit_counts),
        }

    def write(self) -> None:
        payload = self.to_payload()
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        target = self.records_dir / f"{self.date}.json"
        atomic_write_text(target, rendered, encoding="utf-8")
        self._write_sqlite(rendered)

    def _write_sqlite(self, payload_json: str) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_records (
                    run_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT NOT NULL,
                    output_quality TEXT NOT NULL,
                    failure_reason TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_records_date_kind ON run_records(date, kind)"
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO run_records (
                    run_id, date, kind, trigger, started_at_utc, finished_at_utc,
                    output_quality, failure_reason, payload_json, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    self.date,
                    self.kind,
                    self.trigger,
                    self.started_at_utc,
                    self.finished_at_utc,
                    self.output_quality,
                    self.failure_reason,
                    payload_json,
                    _utc_now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
