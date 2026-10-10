"""KeyPulse's own capture streams as queryable records (D26).

One explicit list of the raw_events.event_type values that capture writes and that granted AI hosts
may search. idle_start / idle_end carry no content and ocr_text_capture / manual_save are not part of
the D26 scope, so they are deliberately absent.
"""
from __future__ import annotations

CAPTURE_SEARCHABLE_EVENT_TYPES: tuple[str, ...] = (
    "keyboard_chunk",
    "window_focus_session",
    "clipboard_copy",
    "ax_text_capture",
    "browser_url_capture",
)

# Evidence for a capture event is KeyPulse's own record: the locator names the event itself, so it
# is always available (there is no external original that could disappear).
KEYPULSE_LOCATOR_PREFIX = "keypulse://event/"


def keypulse_locator(event_id: int) -> str:
    return f"{KEYPULSE_LOCATOR_PREFIX}{event_id}"


def is_keypulse_locator(locator: str | None) -> bool:
    return bool(locator) and locator.startswith(KEYPULSE_LOCATOR_PREFIX)
