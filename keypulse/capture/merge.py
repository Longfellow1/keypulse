from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher


SOURCE_PRIORITY = {
    "manual": 5,
    "clipboard": 4,
    "ax_text": 3,
    "ocr_text": 2,
    "keyboard_chunk": 1,
}


def normalize_text_for_merge(text: str | None) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def source_priority(source: str | None) -> int:
    return SOURCE_PRIORITY.get(source or "", 0)


def similarity_ratio(left: str | None, right: str | None) -> float:
    normalized_left = normalize_text_for_merge(left)
    normalized_right = normalize_text_for_merge(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    return SequenceMatcher(a=normalized_left, b=normalized_right).ratio()


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
