from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(path: str | Path | None) -> Path:
    return Path(path or "~/.keypulse/theme-state.json").expanduser()


@dataclass(frozen=True)
class ThemeProfile:
    theme_name: str
    version: int = 1
    instructions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)


def read_theme_profile(path: str | Path | None = None) -> ThemeProfile:
    state_path = _path(path)
    if not state_path.exists():
        return ThemeProfile(theme_name="general", version=1, instructions=[])
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return ThemeProfile(theme_name="general", version=1, instructions=[])
    return ThemeProfile(
        theme_name=str(payload.get("theme_name") or "general"),
        version=int(payload.get("version") or 1),
        instructions=[str(item) for item in payload.get("instructions") or []],
        updated_at=str(payload.get("updated_at") or _now()),
    )


def write_theme_profile(
    path: str | Path | None,
    *,
    theme_name: str,
    version: int,
    instructions: list[str],
) -> ThemeProfile:
    profile = ThemeProfile(theme_name=theme_name, version=version, instructions=list(instructions))
    state_path = _path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "theme_name": profile.theme_name,
                "version": profile.version,
                "instructions": profile.instructions,
                "updated_at": profile.updated_at,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return profile


def record_theme_refine(path: str | Path | None, *, theme_name: str, instruction: str) -> ThemeProfile:
    current = read_theme_profile(path)
    if current.theme_name != theme_name:
        current = ThemeProfile(theme_name=theme_name, version=0, instructions=[])
    updated = ThemeProfile(
        theme_name=theme_name,
        version=current.version + 1,
        instructions=[*current.instructions, instruction],
    )
    return write_theme_profile(path, theme_name=updated.theme_name, version=updated.version, instructions=updated.instructions)


def theme_summary_patch(profile: ThemeProfile) -> str:
    recent = profile.instructions[-3:]
    return "\n".join(
        [
            f"theme={profile.theme_name}",
            f"version={profile.version}",
            *[f"instruction={item}" for item in recent],
        ]
    )

