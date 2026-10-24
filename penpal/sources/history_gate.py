"""Gate for historical source imports until the user has run onboarding (#32 follow-up).

Historical imports are the registry plugins (claude_code, chrome_history, safari_history, zsh_history, ...). They
stay off until the user chooses sources in `penpal install onboard`. Live capture (window, clipboard, browser URL,
keyboard, AX) is not a registry source and is never gated (D26).

Existing installs must not change: when the marker is absent but history from a registered source is already
stored, the marker is set once (migration) and the gate opens. A fresh install has neither, so it stays closed.
config.toml is not used as evidence because `penpal install init` writes it on fresh installs too.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from penpal.capture.capture_control import disabled_sources
from penpal.sources.registry import list_sources
from penpal.store.repository import get_conn, get_state, set_state
from penpal.utils.logging import get_logger

LOGGER = get_logger("sources.history_gate")

ONBOARDED_KEY = "sources_onboarded"
WAITING_TEXT = "历史导入等待首次设置：运行 penpal install onboard"
WAITING_TEXT_EN = "History import waits for first setup: run penpal install onboard"


def mark_onboarded(via: str) -> None:
    """Record that the user has made a source choice (onboarding) or that an existing install was detected."""
    set_state(ONBOARDED_KEY, json.dumps({"via": via, "at": datetime.now(timezone.utc).isoformat()}, sort_keys=True))


def historical_imports_allowed() -> bool:
    """True once onboarding ran, or when an existing install is detected (then the marker is set once).
    Any read error keeps the gate closed."""
    try:
        if get_state(ONBOARDED_KEY):
            return True
        if _has_pre_existing_history():
            mark_onboarded("migration")
            LOGGER.info("historical import gate opened for an existing install (migration)")
            return True
    except Exception:
        LOGGER.exception("historical import gate check failed; keeping history imports off")
    return False


def _has_pre_existing_history() -> bool:
    names = [plugin.name for plugin in list_sources()]
    if not names:
        return False
    conn = get_conn()
    marks = ",".join("?" for _ in names)
    if conn.execute(f"SELECT 1 FROM sources WHERE type IN ({marks}) LIMIT 1", names).fetchone():
        return True
    if conn.execute(f"SELECT 1 FROM raw_events WHERE source IN ({marks}) LIMIT 1", names).fetchone():
        return True
    return bool(set(disabled_sources()) & set(names))


__all__ = ["ONBOARDED_KEY", "WAITING_TEXT", "historical_imports_allowed", "mark_onboarded"]
