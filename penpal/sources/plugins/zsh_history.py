"""zsh extended-history reader.

zsh "metafies" its history file: any byte that is 0x83 or in the 0x80-0x9f control range (and a few
ASCII markers) is written as 0x83 followed by (byte XOR 0x20). Multi-byte UTF-8 such as Chinese text
contains such bytes, so the raw file is not valid UTF-8 until it is unmetafied. Decoding it as-is gave
mojibake (D27 / issue #53). ``unmetafy`` reverses the escape on the raw bytes before the UTF-8 decode.

Sync state (``use_cursor=True``): the plugin is window-based (``since``/``until``), so it keeps one
cursor flag, ``decoded: 1``, per history file. Without the flag (a fresh install, or after migration
v26 deleted the previously mis-decoded rows) the first sync reads the *whole* file once, then stores
the flag. Re-reading is safe: an event's dedup key (time + command + ``zsh:line:N``) depends only on
the file content, so it is identical across reads.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from penpal.sources.availability import Availability, check_file
from penpal.sources.cleaning.content_quality import is_low_signal_event
from penpal.sources.types import DataSource, DataSourceInstance, SemanticEvent


_EXTENDED_PATTERN = re.compile(r"^: (?P<ts>\d+):(?P<elapsed>\d+);(?P<command>.*)$", re.DOTALL)
_META = 0x83
CURSOR_VERSION = 1


def unmetafy(data: bytes) -> bytes:
    """Reverse zsh's metafication: ``0x83 b`` -> ``b ^ 0x20``. A trailing lone 0x83 is kept as-is."""
    if _META not in data:
        return data
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == _META and i + 1 < n:
            out.append(data[i + 1] ^ 0x20)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def decode_history_line(raw: bytes) -> str:
    """One raw history line -> text: unmetafy, UTF-8 decode (lossy), no line terminator."""
    return unmetafy(raw.rstrip(b"\r\n")).decode("utf-8", errors="replace")


def _clean_command(command: str) -> str:
    # A multi-line command is stored with each inner newline escaped as "\<newline>"; the physical
    # line therefore ends with a lone continuation backslash that is not part of the command.
    command = command.rstrip()
    if command.endswith("\\") and not command.endswith("\\\\"):
        command = command[:-1].rstrip()
    return command


class ZshHistorySource(DataSource):
    name = "zsh_history"
    privacy_tier = "green"
    liveness = "always"
    fast_poll = True
    cheap_probe = True
    description = "zsh extended history reader"

    def __init__(self, *, history_path: Path | None = None, use_cursor: bool = False) -> None:
        self._history_path = (history_path or (Path.home() / ".zsh_history")).expanduser()
        self._use_cursor = use_cursor
        self._pending_cursors: dict[int, dict[str, Any]] = {}

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

    def availability(self) -> Availability:
        return check_file(self._history_path)

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        history_path = Path(instance.locator).expanduser()
        if not history_path.exists() or not history_path.is_file():
            return iter(())

        if self._use_cursor:
            from penpal.store.sync_repo import ensure_source, get_cursor

            source_id = ensure_source(self.name, str(history_path.resolve()))
            if get_cursor(source_id).get("decoded") != CURSOR_VERSION:
                since = datetime.min.replace(tzinfo=timezone.utc)  # full re-read, once
            self._pending_cursors[source_id] = {"decoded": CURSOR_VERSION}

        evidence_path = str(history_path.resolve())

        def _iter_events() -> Iterator[SemanticEvent]:
            try:
                handle = history_path.open("rb")
            except Exception:
                return
            with handle:
                for line_idx, raw in enumerate(handle, start=1):
                    match = _EXTENDED_PATTERN.match(decode_history_line(raw))
                    if match is None:
                        continue

                    ts = int(match.group("ts"))
                    elapsed = int(match.group("elapsed"))
                    command = _clean_command(match.group("command"))
                    if not command:
                        continue
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
                        evidence_locator=evidence_path,
                        evidence_line=line_idx,
                    )
                    is_noise, _ = is_low_signal_event(event)
                    if is_noise:
                        continue
                    yield event

        return _iter_events()

    def commit_cursors(self, *, failed: bool = False) -> None:
        """Persist the 'decoded' flag once the registry has stored the events."""
        from penpal.store.sync_repo import record_sync_error, save_cursor

        pending, self._pending_cursors = self._pending_cursors, {}
        for source_id, cursor in pending.items():
            if failed:
                record_sync_error(source_id, "persist failed; cursor not advanced")
            else:
                save_cursor(source_id, cursor)
