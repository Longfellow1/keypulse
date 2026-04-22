from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from keypulse.integrations.sinks import resolve_active_sink


@dataclass
class _ObsidianConfig:
    vault_path: str
    vault_name: str = "KeyPulse"


@dataclass
class _IntegrationConfig:
    standalone_output_path: str
    state_path: str


@dataclass
class _Config:
    obsidian: _ObsidianConfig
    integration: _IntegrationConfig


def test_resolve_active_sink_prefers_obsidian_vault(tmp_path: Path):
    home = tmp_path
    vault = home / "Go" / "Knowledge"
    (vault / ".obsidian").mkdir(parents=True)
    app_support = home / "Library" / "Application Support" / "obsidian"
    app_support.mkdir(parents=True)
    (app_support / "obsidian.json").write_text(
        json.dumps(
            {
                "vaults": {
                    "abc": {
                        "open": True,
                        "path": str(vault),
                    }
                }
            }
        )
    )

    cfg = _Config(
        obsidian=_ObsidianConfig(vault_path=str(vault)),
        integration=_IntegrationConfig(
            standalone_output_path=str(home / "Standalone"),
            state_path=str(home / "sink-state.json"),
        ),
    )

    sink = resolve_active_sink(config=cfg, home=home)

    assert sink.kind == "obsidian"
    assert sink.output_dir == vault
    assert sink.source == "filesystem"


def test_resolve_active_sink_falls_back_to_standalone(tmp_path: Path):
    cfg = _Config(
        obsidian=_ObsidianConfig(vault_path=str(tmp_path / "Missing" / "Knowledge")),
        integration=_IntegrationConfig(
            standalone_output_path=str(tmp_path / "Standalone"),
            state_path=str(tmp_path / "sink-state.json"),
        ),
    )

    sink = resolve_active_sink(config=cfg, home=tmp_path)

    assert sink.kind == "standalone"
    assert sink.output_dir == tmp_path / "Standalone"
    assert sink.source == "fallback"
