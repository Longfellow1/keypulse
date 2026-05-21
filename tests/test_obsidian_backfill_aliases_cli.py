from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

import keypulse.i18n as i18n
from keypulse.cli import main


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            block = "\n".join(lines[1:idx]).strip()
            return yaml.safe_load(block) or {}
    return {}


def test_obsidian_backfill_aliases_adds_missing_and_preserves_existing(monkeypatch, tmp_path):
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    daily_dir = vault / "Daily"
    weekly_dir = vault / "Weekly"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)

    (anchors_dir / "keypulse-p1.md").write_text(
        "\n".join(
            [
                "---",
                'anchor_id: "keypulse-p1"',
                'display: "KeyPulse P1 修复"',
                'started: "2026-05-18"',
                'last_active: "2026-05-21"',
                'state: "active"',
                "---",
                "",
                "## Timeline",
            ]
        ),
        encoding="utf-8",
    )
    (anchors_dir / "keypulse-p2.md").write_text(
        "\n".join(
            [
                "---",
                'anchor_id: "keypulse-p2"',
                'display: "KeyPulse P2 修复"',
                'aliases: ["手工别名"]',
                "---",
                "",
                "## Timeline",
            ]
        ),
        encoding="utf-8",
    )
    (anchors_dir / "keypulse-p3.md").write_text(
        "\n".join(
            [
                "---",
                'anchor_id: "keypulse-p3"',
                'display: "KeyPulse P3 修复"',
                'aliases: ["已有别名"]',
                "tags:",
                '  - "anchor"',
                "---",
                "",
                "## Timeline",
            ]
        ),
        encoding="utf-8",
    )

    (daily_dir / "2026-05-21.md").write_text("# 2026-05-21\n\nold daily\n", encoding="utf-8")
    (daily_dir / "2026-05-22.md").write_text(
        "\n".join(
            [
                "---",
                'aliases: ["手工日报别名"]',
                "tags:",
                '  - "daily"',
                "---",
                "",
                "# 2026-05-22",
            ]
        ),
        encoding="utf-8",
    )

    (weekly_dir / "2026-W21.md").write_text("# 本周工作汇报 (2026-W21)\n", encoding="utf-8")
    (weekly_dir / "2026-W22.md").write_text(
        "\n".join(
            [
                "---",
                'aliases: ["手工周报别名"]',
                "---",
                "",
                "# 这周 (2026-W22)",
            ]
        ),
        encoding="utf-8",
    )

    cfg = type(
        "Cfg",
        (),
        {"obsidian": type("Obs", (), {"vault_path": str(vault)})()},
    )()
    monkeypatch.setattr("keypulse.cli.get_config", lambda: cfg)
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "backfill-aliases"])

    assert result.exit_code == 0
    assert "scanned=7 changed=5 skipped=2 alias_skipped=4" in result.output
    assert "anchors: scanned=3 changed=2 skipped=1 alias_skipped=2" in result.output
    assert "daily: scanned=2 changed=1 skipped=1 alias_skipped=1" in result.output
    assert "weekly: scanned=2 changed=2 skipped=0 alias_skipped=1" in result.output

    anchor1 = _read_frontmatter(anchors_dir / "keypulse-p1.md")
    assert anchor1.get("aliases") == ["KeyPulse P1 修复"]
    assert "tags" in anchor1

    anchor2 = _read_frontmatter(anchors_dir / "keypulse-p2.md")
    assert anchor2.get("aliases") == ["手工别名"]
    assert "tags" in anchor2

    daily1 = _read_frontmatter(daily_dir / "2026-05-21.md")
    assert daily1.get("aliases") == ["2026-05-21 周四"]
    assert daily1.get("tags") == ["daily", "daily/2026-W21"]

    weekly1 = _read_frontmatter(weekly_dir / "2026-W21.md")
    assert weekly1.get("aliases") == ["2026-W21 (5/18–5/24)"]
    assert weekly1.get("tags") == ["weekly", "weekly/exec"]

    weekly2 = _read_frontmatter(weekly_dir / "2026-W22.md")
    assert weekly2.get("aliases") == ["手工周报别名"]
    assert weekly2.get("tags") == ["weekly", "weekly/plain"]
