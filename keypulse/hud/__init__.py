from keypulse.hud.health import HEALTH_JSON_PATH, health_status_emoji, read_health
from keypulse.hud.state import (
    HUDState,
    add_attention_item,
    read_hud_state,
    remove_attention_item,
    set_hud_mode,
    set_today_focus,
)
from keypulse.hud.summary import build_hud_snapshot


def run_hud(*args, **kwargs):
    from keypulse.hud.app import run_hud as _run_hud

    return _run_hud(*args, **kwargs)


__all__ = [
    "HEALTH_JSON_PATH",
    "HUDState",
    "add_attention_item",
    "build_hud_snapshot",
    "health_status_emoji",
    "read_health",
    "read_hud_state",
    "remove_attention_item",
    "run_hud",
    "set_hud_mode",
    "set_today_focus",
]
