"""Persistence bridge between CapabilityRegistry and the SQLite state repo.

The state repo is the runtime authority for capability health: supervisor writes
here every interval, and HUD/healthcheck read from here. health.json is a
periodic snapshot produced by the healthcheck CLI for external consumers
(launchd watchdog, ops tooling); it is not the runtime source of truth.
"""
from __future__ import annotations

import json
from typing import Any

from keypulse.capabilities.base import HealthState
from keypulse.store.repository import get_state, set_state

CAPABILITIES_STATE_KEY = "capabilities"


def _serialize_state(state: HealthState) -> dict[str, Any]:
    return {
        "ok": state.ok,
        "code": state.code,
        "last_checked": state.last_checked,
        "detail": state.detail,
    }


def _deserialize_state(value: dict[str, Any]) -> HealthState | None:
    try:
        return HealthState(
            ok=bool(value.get("ok")),
            code=str(value.get("code") or ""),
            last_checked=float(value.get("last_checked") or 0.0),
            detail=str(value["detail"]) if value.get("detail") is not None else None,
        )
    except (TypeError, ValueError):
        return None


def save_states(states: dict[str, HealthState]) -> None:
    payload = {name: _serialize_state(state) for name, state in states.items()}
    set_state(CAPABILITIES_STATE_KEY, json.dumps(payload, ensure_ascii=False))


def load_states() -> dict[str, HealthState]:
    payload = load_states_raw()
    states: dict[str, HealthState] = {}
    for name, value in payload.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        state = _deserialize_state(value)
        if state is not None:
            states[name] = state
    return states


def load_states_raw() -> dict[str, dict[str, Any]]:
    """Return the raw serialised dict (for embedding into health.json snapshot)."""
    raw = get_state(CAPABILITIES_STATE_KEY)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
