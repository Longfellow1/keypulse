"""Persistent capture switches: global pause and per-source disable (#28).

Both live in the existing ``app_state`` key-value table, the durable store the daemon,
CLI and HUD already share. No new file and no DB migration.

- ``status``: "running" | "paused" | "stopped" (global pause is ``status == "paused"``).
- ``disabled_sources``: JSON list of source names (e.g. ``["keyboard_chunk"]``).

``source_writes_allowed()`` is the write gate. Every path that persists new content from a
live watcher or a source import checks it right before writing. In-memory watcher pauses
only stop work early; they are not the guarantee.
"""

from __future__ import annotations

import json

from keypulse.store.repository import get_state, set_state

PAUSED_STATUS = "paused"
DISABLED_SOURCES_KEY = "disabled_sources"

# Live capture watcher names, which are also the ``source`` of the events they emit.
CAPTURE_SOURCES = frozenset(
    {
        "window",
        "idle",
        "clipboard",
        "manual",
        "keyboard_chunk",
        "ax_text",
        "browser",
        "browser_url",
        "browser_history",
    }
)


def is_capture_paused() -> bool:
    return (get_state("status") or "").strip() == PAUSED_STATUS


def disabled_sources() -> frozenset[str]:
    """Return disabled source names. Malformed stored JSON reads as "nothing disabled"."""
    raw = (get_state(DISABLED_SOURCES_KEY) or "").strip()
    if not raw:
        return frozenset()
    try:
        value = json.loads(raw)
    except ValueError:
        return frozenset()
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(item).strip() for item in value if str(item).strip())


def set_source_enabled(name: str, enabled: bool) -> None:
    current = set(disabled_sources())
    if enabled:
        current.discard(name)
    else:
        current.add(name)
    set_state(DISABLED_SOURCES_KEY, json.dumps(sorted(current), ensure_ascii=False))


def source_writes_allowed(source: str | None) -> bool:
    """False while paused or when this source is switched off. Read errors propagate (fail closed)."""
    if is_capture_paused():
        return False
    return (source or "") not in disabled_sources()
