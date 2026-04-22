from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keypulse.config import Config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def _obsidian_app_support(home: Path) -> Path:
    return home / "Library" / "Application Support" / "obsidian" / "obsidian.json"


@dataclass(frozen=True)
class SinkTarget:
    kind: str
    output_dir: Path
    source: str
    display_name: str = ""
    detected_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "SinkTarget":
        return SinkTarget(
            kind=self.kind,
            output_dir=_expand(self.output_dir),
            source=self.source,
            display_name=self.display_name or self.kind,
            detected_at=self.detected_at or _now(),
            metadata=dict(self.metadata),
        )


def _candidate_from_obsidian_config(config: Config) -> SinkTarget | None:
    vault_path = _expand(config.obsidian.vault_path)
    if (vault_path / ".obsidian").exists():
        return SinkTarget(
            kind="obsidian",
            output_dir=vault_path,
            source="config",
            display_name=config.obsidian.vault_name or "Obsidian",
            metadata={"origin": "config"},
        )
    return None


def _candidate_from_obsidian_runtime(home: Path, config: Config) -> SinkTarget | None:
    obsidian_state = _obsidian_app_support(home)
    if not obsidian_state.exists():
        return None
    try:
        payload = json.loads(obsidian_state.read_text())
    except Exception:
        return None

    vaults = payload.get("vaults") or {}
    for vault in vaults.values():
        if not vault.get("open"):
            continue
        vault_path = _expand(vault.get("path", ""))
        if vault_path and (vault_path / ".obsidian").exists():
            return SinkTarget(
                kind="obsidian",
                output_dir=vault_path,
                source="filesystem",
                display_name=config.obsidian.vault_name or vault_path.name or "Obsidian",
                metadata={"origin": "obsidian.json"},
            )
    return None


def _standalone_candidate(config: Config) -> SinkTarget:
    vault_path = _expand(config.integration.standalone_output_path)
    return SinkTarget(
        kind="standalone",
        output_dir=vault_path,
        source="fallback",
        display_name="Standalone",
        metadata={"origin": "fallback"},
    )


def _is_valid_persisted_sink(target: SinkTarget) -> bool:
    if target.kind != "obsidian":
        return True
    return (target.output_dir / ".obsidian").exists()


def resolve_active_sink(
    config: Config | None = None,
    *,
    home: Path | None = None,
    state_path: str | Path | None = None,
    persist: bool = False,
) -> SinkTarget:
    cfg = config or Config.load()
    home_dir = home or Path.home()
    sink_state_path = _expand(state_path or cfg.integration.state_path)

    persisted = None
    if sink_state_path.exists():
        try:
            from keypulse.integrations.state import read_sink_state

            loaded = read_sink_state(sink_state_path)
            persisted = loaded if _is_valid_persisted_sink(loaded) else None
        except Exception:
            persisted = None

    candidates = [
        persisted,
        _candidate_from_obsidian_runtime(home_dir, cfg),
        _candidate_from_obsidian_config(cfg),
        _standalone_candidate(cfg),
    ]

    chosen = next((candidate for candidate in candidates if candidate is not None), _standalone_candidate(cfg))
    chosen = chosen.normalized()

    if persist:
        from keypulse.integrations.state import write_sink_state

        write_sink_state(sink_state_path, chosen)

    return chosen
