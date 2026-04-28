from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from keypulse.sources.cleaning.content_quality import is_low_signal_event
from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


_EXTENDED_PATTERN = re.compile(r"^: (?P<ts>\d+):(?P<elapsed>\d+);(?P<command>.*)$")


class ZshHistorySource(DataSource):
    name = "zsh_history"
    privacy_tier = "green"
    liveness = "always"
    description = "zsh extended history reader"

    def __init__(self, *, history_path: Path | None = None) -> None:
        self._history_path = (history_path or (Path.home() / ".zsh_history")).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._history_path.exists():
            return []
        return [
            DataSourceInstance(
                plugin=self.name,
                locator=str(self._history_path.resolve()),
                label=".zsh_history",
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
                for line_idx, line in enumerate(handle, start=1):
                    match = _EXTENDED_PATTERN.match(line.rstrip("\n"))
                    if match is None:
                        continue

                    ts = int(match.group("ts"))
                    elapsed = int(match.group("elapsed"))
                    command = match.group("command")
                    event_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if event_time < since or event_time > until:
                        continue

                    event = SemanticEvent(
                        time=event_time,
                        source=self.name,
                        actor="user",
                        intent=command[:200],
                        artifact="shell:zsh",
                        raw_ref=f"zsh:line:{line_idx}",
                        privacy_tier=self.privacy_tier,
                        metadata={"elapsed_seconds": elapsed},
                    )
                    is_noise, _ = is_low_signal_event(event)
                    if is_noise:
                        continue
                    yield event

        return _iter_events()
