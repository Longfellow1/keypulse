from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000


class ChromeHistorySource(DataSource):
    name = "chrome_history"
    privacy_tier = "green"
    liveness = "app_running"
    description = "Chrome visit history reader"

    def __init__(self, *, profiles_root: Path | None = None) -> None:
        self._profiles_root = (
            profiles_root or (Path.home() / "Library" / "Application Support" / "Google" / "Chrome")
        ).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._profiles_root.exists() or not self._profiles_root.is_dir():
            return []

        instances: list[DataSourceInstance] = []
        for profile_dir in sorted(self._profiles_root.iterdir()):
            if not profile_dir.is_dir():
                continue
            history_path = profile_dir / "History"
            if not history_path.exists():
                continue
            profile = profile_dir.name
            instances.append(
                DataSourceInstance(
                    plugin=self.name,
                    locator=str(history_path.resolve()),
                    label=profile,
                    metadata={"profile": profile},
                )
            )
        return instances

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        history_path = Path(instance.locator).expanduser()
        if not history_path.exists() or not history_path.is_file():
            return iter(())

        since_us = int(since.astimezone(timezone.utc).timestamp() * 1_000_000) + _WEBKIT_EPOCH_OFFSET_US
        until_us = int(until.astimezone(timezone.utc).timestamp() * 1_000_000) + _WEBKIT_EPOCH_OFFSET_US
        profile = str(instance.metadata.get("profile") or history_path.parent.name)

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
                        SELECT visits.id, urls.url, urls.title, visits.visit_time
                        FROM visits
                        JOIN urls ON visits.url = urls.id
                        WHERE visits.visit_time BETWEEN ? AND ?
                        ORDER BY visits.visit_time ASC
                        """,
                        (since_us, until_us),
                    )
                    rows = cursor.fetchall()
                except Exception:
                    return
                finally:
                    if conn is not None:
                        conn.close()

                for visit_id, url, title, visit_time in rows:
                    if not isinstance(visit_time, (int, float)):
                        continue
                    unix_us = int(visit_time) - _WEBKIT_EPOCH_OFFSET_US
                    event_time = datetime.fromtimestamp(unix_us / 1_000_000, tz=timezone.utc)
                    full_url = str(url or "")
                    intent = str(title or full_url)[:200]
                    yield SemanticEvent(
                        time=event_time,
                        source=self.name,
                        actor="user",
                        intent=intent,
                        artifact=full_url,
                        raw_ref=f"chrome:visit:{visit_id}",
                        privacy_tier=self.privacy_tier,
                        metadata={"profile": profile, "full_url": full_url},
                    )

        return _iter_events()
