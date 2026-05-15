from __future__ import annotations

import json
from pathlib import Path

from keypulse.sources.discoverers.markdown_vault import discover_markdown_vault_candidates


def test_discover_markdown_vault_candidates_from_obsidian_config(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    vault = home / "Notes" / "VaultA"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "daily.md").write_text("# daily\n", encoding="utf-8")

    obsidian_config = home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    obsidian_config.parent.mkdir(parents=True, exist_ok=True)
    obsidian_config.write_text(
        json.dumps({"vaults": {"a": {"path": str(vault)}}}),
        encoding="utf-8",
    )

    candidates = discover_markdown_vault_candidates(exclude_paths=set())
    by_path = {item.path: item for item in candidates}

    assert str(vault.resolve()) in by_path
    candidate = by_path[str(vault.resolve())]
    assert candidate.discoverer == "markdown_vault"
    assert candidate.shape == "document_file"
    assert candidate.app_hint == "Obsidian"
    assert candidate.confidence == "high"


def test_discover_markdown_vault_candidates_from_density(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    dense = home / "Research"
    dense.mkdir(parents=True, exist_ok=True)
    for idx in range(8):
        (dense / f"note-{idx}.md").write_text("# note\n", encoding="utf-8")
    for idx in range(2):
        (dense / f"bin-{idx}.dat").write_text("x", encoding="utf-8")

    candidates = discover_markdown_vault_candidates(exclude_paths=set())
    by_path = {item.path: item for item in candidates}

    assert str(dense.resolve()) in by_path
    candidate = by_path[str(dense.resolve())]
    assert candidate.shape == "document_file"
    assert candidate.confidence == "medium"


def test_discover_markdown_vault_candidates_respects_excluded(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    vault = home / "Docs"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "x.md").write_text("# x\n", encoding="utf-8")

    candidates = discover_markdown_vault_candidates(exclude_paths={str(vault.resolve())})
    assert candidates == []
