from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_ts() -> float:
    return time.time()


def iso_to_unix(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def truthy_env(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def _safe_get_state(key: str) -> str:
    try:
        from keypulse.store.repository import get_state

        return get_state(key) or ""
    except Exception:
        return ""


def _watchers_payload() -> dict[str, Any]:
    raw = _safe_get_state("capture_runtime")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    watchers = payload.get("watchers")
    return watchers if isinstance(watchers, dict) else {}


def watcher_healthy(name: str) -> bool:
    """True iff watcher exists, running, no last_error, no crashes, not gave_up."""
    watchers = _watchers_payload()
    entry = watchers.get(name)
    if not isinstance(entry, dict):
        return False
    if not entry.get("running"):
        return False
    if entry.get("gave_up"):
        return False
    if entry.get("last_error"):
        return False
    if int(entry.get("crashes") or 0) > 0:
        return False
    return True


def capture_pipeline_healthy(required_watchers: list[str]) -> bool:
    """True iff capture_error_code empty AND all required watchers are healthy."""
    code = _safe_get_state("capture_error_code").strip()
    if code:
        return False
    return all(watcher_healthy(name) for name in required_watchers)
