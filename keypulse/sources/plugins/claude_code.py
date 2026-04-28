from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


class ClaudeCodeSource(DataSource):
    name = "claude_code"
    privacy_tier = "green"
    liveness = "always"
    description = "Claude Code session JSONL reader"

    def __init__(self, *, projects_root: Path | None = None) -> None:
        self._projects_root = (projects_root or (Path.home() / ".claude" / "projects")).expanduser()

    def discover(self) -> list[DataSourceInstance]:
        if not self._projects_root.exists() or not self._projects_root.is_dir():
            return []

        instances: list[DataSourceInstance] = []
        for project_dir in sorted(self._projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            session_files = list(project_dir.glob("*.jsonl"))
            metadata = {
                "project_path": _decode_project_path(project_dir.name),
                "session_count": len(session_files),
            }
            instances.append(
                DataSourceInstance(
                    plugin=self.name,
                    locator=str(project_dir.resolve()),
                    label=_decode_project_label(project_dir.name),
                    metadata=metadata,
                )
            )
        return instances

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        project_dir = Path(instance.locator).expanduser()
        if not project_dir.exists() or not project_dir.is_dir():
            return iter(())

        def _iter_events() -> Iterator[SemanticEvent]:
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                session_id_default = jsonl_file.stem
                try:
                    handle = jsonl_file.open("r", encoding="utf-8", errors="replace")
                except Exception:
                    continue
                with handle:
                    for line_idx, raw_line in enumerate(handle, start=1):
                        row = _parse_json_line(raw_line)
                        if row is None:
                            continue
                        row_type = row.get("type")
                        if row_type not in {"user", "assistant"}:
                            continue
                        event_time = _parse_iso8601(row.get("timestamp"))
                        if event_time is None:
                            continue
                        if event_time < since or event_time > until:
                            continue

                        intent_text = _extract_message_text(row).strip()
                        if not intent_text:
                            continue

                        session_id = str(row.get("sessionId") or session_id_default)
                        message_id = str(row.get("uuid") or row.get("messageId") or line_idx)
                        yield SemanticEvent(
                            time=event_time,
                            source=self.name,
                            actor=str(row_type),
                            intent=intent_text[:200],
                            artifact=f"claude:session:{session_id}:msg:{message_id}",
                            raw_ref=(
                                f"claude:projects:{project_dir.name}:{jsonl_file.name}:{line_idx}"
                            ),
                            privacy_tier=self.privacy_tier,
                            metadata={
                                "session_id": session_id,
                                "parent_uuid": row.get("parentUuid"),
                                "project_dir": str(project_dir.resolve()),
                            },
                        )

        return _iter_events()


def _parse_json_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_iso8601(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_message_text(row: dict[str, Any]) -> str:
    message = row.get("message")
    text = _coerce_text(message)
    if text:
        return text
    return _coerce_text(row.get("content"))


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        parsed = _try_literal_eval(stripped)
        if parsed is not None:
            nested = _coerce_text(parsed)
            if nested:
                return nested
        return stripped
    if isinstance(value, dict):
        if "content" in value:
            nested = _coerce_text(value.get("content"))
            if nested:
                return nested
        text_value = value.get("text")
        if isinstance(text_value, str):
            return text_value.strip()
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"].strip())
                continue
            nested = _coerce_text(item)
            if nested:
                parts.append(nested)
        return "\n".join(part for part in parts if part).strip()
    return str(value).strip()


def _try_literal_eval(value: str) -> object | None:
    if not value or value[0] not in "[{(":
        return None
    try:
        parsed = ast.literal_eval(value)
    except Exception:
        return None
    if isinstance(parsed, (dict, list, str)):
        return parsed
    return None


def _decode_project_path(encoded: str) -> str:
    if not encoded:
        return ""
    tokens = [token for token in encoded.split("-") if token]
    if not tokens:
        return ""
    return "/" + "/".join(tokens)


def _decode_project_label(encoded: str) -> str:
    path = _decode_project_path(encoded)
    if not path:
        return encoded or "unknown"
    parts = [part for part in path.split("/") if part]
    return parts[-1] if parts else "unknown"
