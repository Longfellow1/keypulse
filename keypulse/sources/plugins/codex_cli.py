from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


class CodexCliSource(DataSource):
    name = "codex_cli"
    privacy_tier = "green"
    liveness = "always"
    description = "Codex CLI history reader"

    def __init__(self, *, history_path: Path | None = None) -> None:
        self._history_path = (history_path or (Path.home() / ".codex" / "history.jsonl")).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._history_path.exists():
            return []
        return [
            DataSourceInstance(
                plugin=self.name,
                locator=str(self._history_path.resolve()),
                label="codex history",
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

        def _iter_events() -> Iterator[SemanticEvent]:
            try:
                handle = history_path.open("r", encoding="utf-8", errors="replace")
            except Exception:
                return

            with handle:
                for line_idx, raw_line in enumerate(handle, start=1):
                    row = _parse_row(raw_line)
                    if row is None:
                        continue
                    session_id = row.get("session_id")
                    ts = row.get("ts")
                    text = row.get("text")
                    if not isinstance(session_id, str) or not session_id:
                        continue
                    if not isinstance(ts, int):
                        continue
                    if not isinstance(text, str):
                        continue

                    event_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if event_time < since or event_time > until:
                        continue

                    yield SemanticEvent(
                        time=event_time,
                        source=self.name,
                        actor="user",
                        intent=text[:200],
                        artifact=f"codex:session:{session_id}",
                        raw_ref=f"codex:history:{line_idx}",
                        privacy_tier=self.privacy_tier,
                        metadata={"session_id": session_id},
                    )

        return _iter_events()


def _parse_row(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
