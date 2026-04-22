from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from keypulse.utils.dates import resolve_local_date
from keypulse.utils.paths import get_data_dir


HUDMode = Literal["standard", "focus", "sensitive", "review"]


def _state_path(path: str | Path | None = None) -> Path:
    if path is None:
        return get_data_dir() / "hud-state.json"
    return Path(path).expanduser()


@dataclass(frozen=True)
class HUDState:
    mode: HUDMode = "standard"
    today_focus: dict[str, str] = field(default_factory=dict)
    attention_items: list[str] = field(default_factory=list)


def read_hud_state(path: str | Path | None = None) -> HUDState:
    state_path = _state_path(path)
    if not state_path.exists():
        return HUDState()
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return HUDState()
    return HUDState(
        mode=str(payload.get("mode") or "standard"),
        today_focus={
            str(key): str(value)
            for key, value in dict(payload.get("today_focus") or {}).items()
            if str(value).strip()
        },
        attention_items=[
            str(item).strip()
            for item in list(payload.get("attention_items") or [])
            if str(item).strip()
        ],
    )


def write_hud_state(state: HUDState, path: str | Path | None = None) -> HUDState:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2))
    return state


def set_hud_mode(mode: HUDMode, path: str | Path | None = None) -> HUDState:
    current = read_hud_state(path)
    updated = HUDState(
        mode=mode,
        today_focus=current.today_focus,
        attention_items=current.attention_items,
    )
    return write_hud_state(updated, path)


def set_today_focus(text: str, *, date_str: str | None = None, path: str | Path | None = None) -> HUDState:
    current = read_hud_state(path)
    effective_date = resolve_local_date(date=date_str)
    today_focus = dict(current.today_focus)
    if text.strip():
        today_focus[effective_date] = text.strip()
    else:
        today_focus.pop(effective_date, None)
    updated = HUDState(
        mode=current.mode,
        today_focus=today_focus,
        attention_items=current.attention_items,
    )
    return write_hud_state(updated, path)


def add_attention_item(label: str, path: str | Path | None = None) -> HUDState:
    current = read_hud_state(path)
    value = label.strip()
    if not value:
        return current
    items = list(current.attention_items)
    if value not in items:
        items.append(value)
    updated = HUDState(
        mode=current.mode,
        today_focus=current.today_focus,
        attention_items=items[:7],
    )
    return write_hud_state(updated, path)


def remove_attention_item(label: str, path: str | Path | None = None) -> HUDState:
    current = read_hud_state(path)
    value = label.strip()
    updated = HUDState(
        mode=current.mode,
        today_focus=current.today_focus,
        attention_items=[item for item in current.attention_items if item != value],
    )
    return write_hud_state(updated, path)
