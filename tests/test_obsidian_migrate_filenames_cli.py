from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import _rewrite_wikilinks_in_text, main


def _write_anchor(path: Path, *, anchor_id: str, display: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'anchor_id: "{anchor_id}"',
                f'display: "{display}"',
                'started: "2026-05-18"',
                'last_active: "2026-05-21"',
                'state: "active"',
                "---",
                "",
                "## Timeline",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_rewrite_wikilinks_handles_bare_piped_multiple_and_nested() -> None:
    text = (
        "A [[weekly-v3-rollout|周报 v3]] B [[weekly-v3-rollout]] C [[weekly-v3-rollout#timeline|片段]]\n"
        "\n"
        "Nested [[outer|keep [[inner]] text]] should stay unchanged."
    )
    rewritten, rewrites = _rewrite_wikilinks_in_text(
        text,
        {"weekly-v3-rollout": "周报 v3 设计与落地"},
    )

    assert "[[周报 v3 设计与落地]]" in rewritten
    assert "[[周报 v3 设计与落地#timeline]]" in rewritten
    assert "[[outer|keep [[inner]] text]]" in rewritten
    assert len(rewrites) == 3


def test_obsidian_migrate_filenames_dry_run_with_collision(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    daily_dir = vault / "Daily"
    weekly_dir = vault / "Weekly"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)

    _write_anchor(anchors_dir / "weekly-v3-rollout.md", anchor_id="weekly-v3-rollout", display="周报 v3 设计与落地")
    _write_anchor(anchors_dir / "hud-ocr-fixes.md", anchor_id="hud-ocr-fixes", display="HUD OCR Fixes")
    _write_anchor(anchors_dir / "alpha-one.md", anchor_id="alpha-one", display="冲突名")
    _write_anchor(anchors_dir / "beta-two.md", anchor_id="beta-two", display="冲突名")

    daily_note = daily_dir / "2026-05-21.md"
    daily_note.write_text(
        "[[weekly-v3-rollout|周报 v3 设计与落地]]\n[[hud-ocr-fixes]]\n[[alpha-one|冲突名]]\n",
        encoding="utf-8",
    )
    (weekly_dir / "2026-W21.md").write_text("[[weekly-v3-rollout]]\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "migrate-filenames", "--vault", str(vault)])

    assert result.exit_code == 0
    assert "mode=dry-run" in result.output
    assert "planned_renames=2" in result.output
    assert "collisions=1" in result.output
    assert "wikilink_rewrites=3" in result.output
    assert "- collision path=" in result.output

    assert (anchors_dir / "weekly-v3-rollout.md").exists()
    assert not (anchors_dir / "周报 v3 设计与落地.md").exists()
    assert "[[weekly-v3-rollout|周报 v3 设计与落地]]" in daily_note.read_text(encoding="utf-8")


def test_obsidian_migrate_filenames_apply_rewrites_and_renames(tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    daily_dir = vault / "Daily"
    weekly_dir = vault / "Weekly"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)

    _write_anchor(anchors_dir / "weekly-v3-rollout.md", anchor_id="weekly-v3-rollout", display="周报 v3 设计与落地")
    _write_anchor(anchors_dir / "hud-ocr-fixes.md", anchor_id="hud-ocr-fixes", display="HUD OCR Fixes")

    (daily_dir / "2026-05-21.md").write_text(
        "[[weekly-v3-rollout|周报 v3 设计与落地]] 和 [[hud-ocr-fixes]]\n[[weekly-v3-rollout]]\n",
        encoding="utf-8",
    )
    (weekly_dir / "2026-W21.md").write_text("[[hud-ocr-fixes|HUD OCR Fixes]]\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "migrate-filenames", "--vault", str(vault), "--apply"])

    assert result.exit_code == 0
    assert "mode=apply" in result.output
    assert "planned_renames=2" in result.output

    assert (anchors_dir / "周报 v3 设计与落地.md").exists()
    assert (anchors_dir / "HUD OCR Fixes.md").exists()
    assert not (anchors_dir / "weekly-v3-rollout.md").exists()
    assert not (anchors_dir / "hud-ocr-fixes.md").exists()

    daily_body = (daily_dir / "2026-05-21.md").read_text(encoding="utf-8")
    assert "[[周报 v3 设计与落地]]" in daily_body
    assert "[[HUD OCR Fixes]]" in daily_body
    assert "|" not in daily_body

    weekly_body = (weekly_dir / "2026-W21.md").read_text(encoding="utf-8")
    assert weekly_body.strip() == "[[HUD OCR Fixes]]"
