from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from keypulse.sources.cleaning.file_whitelist import is_blocked_sqlite
from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


_CF_EPOCH_OFFSET_S = 978_307_200
_STREAM_APP_USAGE = "/app/usage"
_STREAM_APP_FOCUS = "/app/inFocus"
_STREAM_NOTIFICATION = "/notification/usage"
_STREAM_SAFARI_HISTORY = "/safari/history"
_ALLOWED_STREAMS = (
    _STREAM_APP_USAGE,
    _STREAM_APP_FOCUS,
    _STREAM_NOTIFICATION,
    _STREAM_SAFARI_HISTORY,
)

_READ_SQL = """
SELECT
    Z_PK,
    ZSTREAMNAME,
    ZSTARTDATE,
    ZENDDATE,
    ZVALUESTRING
FROM ZOBJECT
WHERE ZSTARTDATE BETWEEN ? AND ?
  AND ZSTREAMNAME IN (?, ?, ?, ?)
ORDER BY ZSTARTDATE
LIMIT 5000
"""


class KnowledgeCSource(DataSource):
    name = "knowledgec"
    privacy_tier = "yellow"
    liveness = "always"
    description = "macOS 系统级用户活动数据库（应用使用、通知、Safari）"

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path = (
            db_path
            or (Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db")
        ).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._db_path.exists() or not self._db_path.is_file():
            return []

        blocked, _ = is_blocked_sqlite(self._db_path)
        if blocked:
            return []

        try:
            with self._open_readonly_copy(self._db_path) as conn:
                conn.execute("SELECT 1 FROM ZOBJECT LIMIT 1").fetchone()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError, OSError):
            return []

        path_text = str(self._db_path)
        return [
            DataSourceInstance(
                plugin=self.name,
                locator=path_text,
                label="macOS 系统活动",
                metadata={"path": path_text},
            )
        ]

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        db_path = Path(instance.locator).expanduser()
        if not db_path.exists() or not db_path.is_file():
            return iter(())

        blocked, _ = is_blocked_sqlite(db_path)
        if blocked:
            return iter(())

        since_cf = since.astimezone(timezone.utc).timestamp() - _CF_EPOCH_OFFSET_S
        until_cf = until.astimezone(timezone.utc).timestamp() - _CF_EPOCH_OFFSET_S

        def _iter_events() -> Iterator[SemanticEvent]:
            try:
                with self._open_readonly_copy(db_path) as conn:
                    cursor = conn.execute(_READ_SQL, (since_cf, until_cf, *_ALLOWED_STREAMS))
                    rows = cursor.fetchall()
            except (sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError, OSError):
                return

            for row in rows:
                event = _row_to_event(row, privacy_tier=self.privacy_tier, source=self.name)
                if event is None:
                    continue
                yield event

        return _iter_events()

    @contextmanager
    def _open_readonly_copy(self, db_path: Path) -> Iterator[sqlite3.Connection]:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            shutil.copy2(db_path, tmp.name)
            conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
            try:
                yield conn
            finally:
                conn.close()


def _row_to_event(
    row: tuple[object, object, object, object, object],
    *,
    privacy_tier: str,
    source: str,
) -> SemanticEvent | None:
    z_pk, stream, start_date, end_date, value = row
    if not isinstance(start_date, (int, float)):
        return None
    if not isinstance(stream, str):
        return None

    value_text = str(value or "").strip()
    duration_sec = _duration_seconds(start_date, end_date)

    if stream == _STREAM_APP_USAGE and duration_sec < 1:
        return None

    app_name = _app_name(value_text)
    intent, actor, artifact = _map_stream(
        stream=stream,
        value=value_text,
        app_name=app_name,
        duration_sec=duration_sec,
    )
    if intent is None or actor is None or artifact is None:
        return None

    event_time = datetime.fromtimestamp(float(start_date) + _CF_EPOCH_OFFSET_S, tz=timezone.utc)
    return SemanticEvent(
        time=event_time,
        source=source,
        actor=actor,
        intent=intent,
        artifact=artifact,
        raw_ref=f"knowledgec:{stream}:{z_pk}",
        privacy_tier=privacy_tier,
        metadata={"stream": stream, "duration_sec": duration_sec, "bundle_id": value_text},
    )


def _map_stream(
    *,
    stream: str,
    value: str,
    app_name: str,
    duration_sec: int,
) -> tuple[str | None, str | None, str | None]:
    if stream == _STREAM_APP_USAGE:
        return (f"使用 {app_name}（{duration_sec}s）", "user", f"app:{value}")
    if stream == _STREAM_APP_FOCUS:
        return (f"聚焦 {app_name}", "user", f"app:{value}")
    if stream == _STREAM_NOTIFICATION:
        return (f"通知：{value}", "system", f"notification:{value}")
    if stream == _STREAM_SAFARI_HISTORY:
        return (f"Safari 访问：{value}", "user", value)
    return (None, None, None)


def _duration_seconds(start_date: float, end_date: object) -> int:
    if not isinstance(end_date, (int, float)):
        return 0
    return int(float(end_date) - float(start_date))


def _app_name(bundle_id: str) -> str:
    if not bundle_id:
        return "unknown"
    tail = bundle_id.split(".")[-1]
    collapsed = " ".join(tail.split())
    return collapsed or "unknown"
