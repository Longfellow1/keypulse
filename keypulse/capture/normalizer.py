from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional
from keypulse.store.models import RawEvent
from keypulse.capture.policy import redact_url


WINDOW_FOCUS_EVENT = "window_focus"
WINDOW_TITLE_CHANGED_EVENT = "window_title_changed"
WINDOW_HEARTBEAT_EVENT = "window_heartbeat"
WINDOW_FOCUS_SESSION_EVENT = "window_focus_session"
WINDOW_EVENT_TYPES = {
    WINDOW_FOCUS_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
    WINDOW_HEARTBEAT_EVENT,
    WINDOW_FOCUS_SESSION_EVENT,
}
WINDOW_SESSION_EVENT_TYPES = {
    WINDOW_FOCUS_EVENT,
    WINDOW_TITLE_CHANGED_EVENT,
}
WINDOW_PERSISTED_SESSION_EVENT_TYPES = {
    WINDOW_FOCUS_SESSION_EVENT,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_window_event_type(event_type: str) -> bool:
    return event_type in WINDOW_EVENT_TYPES


def is_window_session_event_type(event_type: str) -> bool:
    return event_type in WINDOW_SESSION_EVENT_TYPES


def is_window_persisted_session_event_type(event_type: str) -> bool:
    return event_type in WINDOW_PERSISTED_SESSION_EVENT_TYPES


def _semantic_weight_for(source: str) -> float:
    """Return semantic weight by source."""
    weights = {
        "keyboard_chunk": 1.0,
        "clipboard": 0.9,
        "manual": 1.0,
        "browser": 0.85,
        "ax_text": 0.8,
        "ax_ime_commit": 0.9,
        "ax_snapshot_fallback": 0.5,
        "ocr_text": 0.4,
        "window_focus_session": 0.2,
    }
    return weights.get(source, 0.5)


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
        semantic_weight=_semantic_weight_for("window"),
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
        semantic_weight=_semantic_weight_for("idle"),
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
        semantic_weight=_semantic_weight_for("clipboard"),
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
        semantic_weight=_semantic_weight_for("manual"),
    )


def normalize_ax_text_event(
    text: str,
    app_name: Optional[str] = None,
    window_title: Optional[str] = None,
    process_name: Optional[str] = None,
    ts_start: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RawEvent:
    return RawEvent(
        source="ax_text",
        event_type="ax_text_capture",
        ts_start=ts_start or _now(),
        app_name=app_name,
        window_title=window_title,
        process_name=process_name,
        content_text=text,
        content_hash=_hash(text),
        metadata_json=json.dumps(metadata) if metadata else None,
        semantic_weight=_semantic_weight_for("ax_text"),
    )


def normalize_ocr_text_event(
    text: str,
    app_name: Optional[str] = None,
    window_title: Optional[str] = None,
    process_name: Optional[str] = None,
    ts_start: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RawEvent:
    return RawEvent(
        source="ocr_text",
        event_type="ocr_text_capture",
        ts_start=ts_start or _now(),
        app_name=app_name,
        window_title=window_title,
        process_name=process_name,
        content_text=text,
        content_hash=_hash(text),
        metadata_json=json.dumps(metadata) if metadata else None,
        semantic_weight=_semantic_weight_for("ocr_text"),
    )


def normalize_keyboard_chunk_event(
    text: str,
    app_name: Optional[str] = None,
    window_title: Optional[str] = None,
    process_name: Optional[str] = None,
    ts_start: Optional[str] = None,
    ts_end: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RawEvent:
    return RawEvent(
        source="keyboard_chunk",
        event_type="keyboard_chunk_capture",
        ts_start=ts_start or _now(),
        ts_end=ts_end,
        app_name=app_name,
        window_title=window_title,
        process_name=process_name,
        content_text=text,
        content_hash=_hash(text),
        metadata_json=json.dumps(metadata) if metadata else None,
        semantic_weight=_semantic_weight_for("keyboard_chunk"),
    )


def normalize_browser_tab_event(
    url: str,
    title: Optional[str] = None,
    browser_name: Optional[str] = None,
    ts_start: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RawEvent:
    redacted_url = redact_url(url)
    tab_hash = hashlib.sha256(redacted_url.encode("utf-8")).hexdigest()[:12]
    event_metadata = {
        "url": redacted_url,
        "title": title,
        "browser_name": browser_name,
        "tab_hash": tab_hash,
    }
    if metadata:
        event_metadata.update(metadata)
    return RawEvent(
        source="browser",
        event_type="browser_tab",
        ts_start=ts_start or _now(),
        app_name=browser_name,
        window_title=title,
        content_text=redacted_url or None,
        content_hash=tab_hash,
        metadata_json=json.dumps(event_metadata) if event_metadata else None,
        semantic_weight=_semantic_weight_for("browser"),
    )
