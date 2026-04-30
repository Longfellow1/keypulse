from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent
from keypulse.store.db import get_conn


class RawEventsSource(DataSource):
    name = "raw_events"
    privacy_tier = "yellow"
    liveness = "always"
    description = "KeyPulse raw_events SQLite reader"

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path = db_path.expanduser() if db_path is not None else None

    def discover(self) -> list[DataSourceInstance]:
        conn, should_close = self._open_conn()
        if conn is None:
            return []
        try:
            if not _has_raw_events_table(conn):
                return []
            if conn.execute("SELECT 1 FROM raw_events LIMIT 1").fetchone() is None:
                return []
            return [
                DataSourceInstance(
                    plugin=self.name,
                    locator=self._locator(conn),
                    label="KeyPulse raw events",
                    metadata={"table": "raw_events"},
                )
            ]
        except Exception:
            return []
        finally:
            if should_close:
                conn.close()

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        conn, should_close = self._open_conn()
        if conn is None:
            return iter(())

        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)
        since_iso = since_utc.isoformat()
        until_iso = until_utc.isoformat()

        try:
            if not _has_raw_events_table(conn):
                return iter(())
            rows = conn.execute(
                """
                SELECT *
                FROM raw_events
                WHERE ts_start >= ? AND ts_start <= ?
                ORDER BY ts_start ASC, id ASC
                """,
                (since_iso, until_iso),
            ).fetchall()
        except Exception:
            return iter(())
        finally:
            if should_close:
                conn.close()

        def _iter_events() -> Iterator[SemanticEvent]:
            for row in rows:
                payload = dict(row) if not isinstance(row, dict) else dict(row)
                event_time = _parse_event_time(payload.get("ts_start")) or _parse_event_time(payload.get("created_at"))
                if event_time is None:
                    continue
                if event_time < since_utc or event_time > until_utc:
                    continue
                metadata_json = _parse_metadata_json(payload.get("metadata_json"))
                event_type = str(payload.get("event_type") or "event").strip() or "event"
                source_channel = str(payload.get("source") or "").strip()
                actor = str(
                    payload.get("app_name")
                    or payload.get("process_name")
                    or source_channel
                    or "system"
                ).strip()
                window_title = str(payload.get("window_title") or "").strip()
                body = str(payload.get("content_text") or payload.get("body") or "").strip()
                tags = _extract_tags(payload, metadata_json)

                metadata: dict[str, Any] = {
                    "source_type": event_type,
                    "source_channel": source_channel,
                    "raw_event": payload,
                }
                if window_title:
                    metadata["window_title"] = window_title
                if body:
                    metadata["body"] = body
                if tags:
                    metadata["tags"] = tags

                for key, value in metadata_json.items():
                    if key not in metadata:
                        metadata[key] = value
                    elif metadata[key] != value:
                        metadata[f"metadata_json_{key}"] = value

                raw_id = payload.get("id")
                raw_ref = (
                    f"raw_events:{raw_id}"
                    if raw_id is not None
                    else f"raw_events:{event_time.isoformat()}:{source_channel}:{event_type}"
                )

                yield SemanticEvent(
                    time=event_time,
                    source=self.name,
                    actor=actor,
                    intent=_build_intent(body, window_title, event_type, tags),
                    artifact=_build_artifact(window_title, tags, source_channel, event_type),
                    raw_ref=raw_ref,
                    privacy_tier=self.privacy_tier,
                    metadata=metadata,
                )

        return _iter_events()

    def _open_conn(self) -> tuple[sqlite3.Connection | None, bool]:
        if self._db_path is not None:
            if not self._db_path.exists() or not self._db_path.is_file():
                return None, False
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            return conn, True
        try:
            return get_conn(), False
        except Exception:
            return None, False

    def _locator(self, conn: sqlite3.Connection) -> str:
        if self._db_path is not None:
            return str(self._db_path.resolve(strict=False))
        try:
            rows = conn.execute("PRAGMA database_list").fetchall()
            for row in rows:
                db_path = row[2] if isinstance(row, tuple) else row["file"]
                if db_path:
                    return str(db_path)
        except Exception:
            return "store:raw_events"
        return "store:raw_events"


def _has_raw_events_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_events' LIMIT 1"
    ).fetchone()
    return row is not None


def _parse_event_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_metadata_json(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    raw = value.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _extract_tags(payload: dict[str, Any], metadata_json: dict[str, Any]) -> list[str]:
    tags_value = payload.get("tags")
    if tags_value is None:
        tags_value = metadata_json.get("tags")
    if isinstance(tags_value, str):
        values = [item.strip() for item in tags_value.split(",")]
        return [item for item in values if item]
    if isinstance(tags_value, list):
        values = [str(item).strip() for item in tags_value if str(item).strip()]
        return values
    return []


def _build_intent(body: str, window_title: str, event_type: str, tags: list[str]) -> str:
    if body:
        return body[:200]
    if window_title:
        return window_title[:200]
    if tags:
        return f"{event_type} {' '.join(tags)}"[:200]
    return event_type[:200] if event_type else "raw_event"


def _build_artifact(window_title: str, tags: list[str], source_channel: str, event_type: str) -> str:
    parts: list[str] = []
    if window_title:
        parts.append(f"window:{window_title}")
    if tags:
        parts.append("tags:" + ",".join(tags))
    if source_channel:
        parts.append(f"source:{source_channel}")
    if not parts:
        parts.append(f"type:{event_type or 'event'}")
    return " | ".join(parts)[:240]
