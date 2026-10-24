"""Atomic daemon runtime record format."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from penpal.utils.paths import get_daemon_record_path

COMPONENT_STATES = frozenset({
    "running", "idle", "gave_up", "missing_permission", "storage_error", "disabled",
})


@dataclass(frozen=True)
class Progress:
    seq: int
    last_done_at: str


@dataclass(frozen=True)
class Component:
    state: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in COMPONENT_STATES:
            raise ValueError(f"invalid runtime component state: {self.state}")


@dataclass(frozen=True)
class RuntimeRecord:
    generation: str
    pid: int
    process_start_time: str
    version: str
    code_fingerprint: str
    started_at: str
    written_at: str
    progress: dict[str, Progress] = field(default_factory=dict)
    components: dict[str, Component] = field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_from_dict(value: Any) -> RuntimeRecord | None:
    if not isinstance(value, dict):
        return None
    required = (
        "generation", "process_start_time", "version", "code_fingerprint", "started_at", "written_at",
    )
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        return None
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
        return None
    progress: dict[str, Progress] = {}
    raw_progress = value.get("progress", {})
    if not isinstance(raw_progress, dict):
        return None
    for name, item in raw_progress.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            return None
        seq, last_done_at = item.get("seq"), item.get("last_done_at")
        if not isinstance(seq, int) or seq < 0 or not isinstance(last_done_at, str):
            return None
        progress[name] = Progress(seq, last_done_at)
    components: dict[str, Component] = {}
    raw_components = value.get("components", {})
    if not isinstance(raw_components, dict):
        return None
    try:
        for name, item in raw_components.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                return None
            components[name] = Component(str(item.get("state", "")), str(item.get("detail", "")))
    except ValueError:
        return None
    return RuntimeRecord(
        generation=value["generation"], pid=value["pid"],
        process_start_time=value["process_start_time"], version=value["version"],
        code_fingerprint=value["code_fingerprint"], started_at=value["started_at"],
        written_at=value["written_at"], progress=progress, components=components,
    )


def read_record(path: Path | None = None) -> RuntimeRecord | None:
    target = path or get_daemon_record_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return _record_from_dict(value)


def write_record(record: RuntimeRecord, path: Path | None = None) -> None:
    target = path or get_daemon_record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
