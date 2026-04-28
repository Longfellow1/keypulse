from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


_CF_EPOCH_OFFSET_S = 978_307_200


class SafariHistorySource(DataSource):
    name = "safari_history"
    privacy_tier = "green"
    liveness = "app_running"
    description = "Safari visit history reader"

    def __init__(self, *, history_path: Path | None = None) -> None:
        self._history_path = (history_path or (Path.home() / "Library" / "Safari" / "History.db")).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._history_path.exists():
            return []
        return [
            DataSourceInstance(
                plugin=self.name,
                locator=str(self._history_path.resolve()),
                label="Safari",
                metadata={},
            )
        ]

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        history_path = Path(instance.locator).expanduser()
        if not history_path.exists() or not history_path.is_file():
            return iter(())

        since_cf = since.astimezone(timezone.utc).timestamp() - _CF_EPOCH_OFFSET_S
        until_cf = until.astimezone(timezone.utc).timestamp() - _CF_EPOCH_OFFSET_S

        def _iter_events() -> Iterator[SemanticEvent]:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                try:
                    shutil.copy2(history_path, tmp.name)
                except Exception:
                    return
                conn: sqlite3.Connection | None = None
                try:
                    conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
                    cursor = conn.execute(
                        """
                        SELECT v.id, v.title, hi.url, v.visit_time
                        FROM history_visits v
                        JOIN history_items hi ON v.history_item = hi.id
                        WHERE v.visit_time BETWEEN ? AND ?
                        ORDER BY v.visit_time ASC
                        """,
                        (since_cf, until_cf),
                    )
                    rows = cursor.fetchall()
                except Exception:
                    return
                finally:
                    if conn is not None:
                        conn.close()

                for visit_id, title, url, visit_time in rows:
                    if not isinstance(visit_time, (int, float)):
                        continue
                    unix_s = float(visit_time) + _CF_EPOCH_OFFSET_S
                    event_time = datetime.fromtimestamp(unix_s, tz=timezone.utc)
                    full_url = str(url or "")
                    intent = str(title or full_url)[:200]
                    yield SemanticEvent(
                        time=event_time,
                        source=self.name,
                        actor="user",
                        intent=intent,
                        artifact=full_url,
                        raw_ref=f"safari:visit:{visit_id}",
                        privacy_tier=self.privacy_tier,
                        metadata={"profile": "Default", "full_url": full_url},
                    )

        return _iter_events()
