from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional
from keypulse.store.models import RawEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalize_window_event(
    event_type: str,
    app_name: Optional[str],
    window_title: Optional[str],
    process_name: Optional[str],
    ts_start: Optional[str] = None,
    ts_end: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RawEvent:
    return RawEvent(
        source="window",
        event_type=event_type,
        ts_start=ts_start or _now(),
        ts_end=ts_end,
        app_name=app_name,
        window_title=window_title,
        process_name=process_name,
        metadata_json=json.dumps(metadata) if metadata else None,
    )


def normalize_idle_event(
    event_type: str,  # idle_start | idle_end
    idle_seconds: float = 0.0,
    ts_start: Optional[str] = None,
) -> RawEvent:
    return RawEvent(
        source="idle",
        event_type=event_type,
        ts_start=ts_start or _now(),
        metadata_json=json.dumps({"idle_seconds": idle_seconds}),
    )


def normalize_clipboard_event(
    text: str,
    app_name: Optional[str] = None,
    ts_start: Optional[str] = None,
) -> RawEvent:
    return RawEvent(
        source="clipboard",
        event_type="clipboard_copy",
        ts_start=ts_start or _now(),
        app_name=app_name,
        content_text=text,
        content_hash=_hash(text),
    )


def normalize_manual_event(
    text: str,
    tags: Optional[str] = None,
    app_name: Optional[str] = None,
    window_title: Optional[str] = None,
    ts_start: Optional[str] = None,
) -> RawEvent:
    return RawEvent(
        source="manual",
        event_type="manual_save",
        ts_start=ts_start or _now(),
        app_name=app_name,
        window_title=window_title,
        content_text=text,
        content_hash=_hash(text),
        metadata_json=json.dumps({"tags": tags}) if tags else None,
    )
