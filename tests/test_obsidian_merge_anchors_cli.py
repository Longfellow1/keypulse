from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import _split_markdown_frontmatter, main


def _write_anchor(
    path: Path,
    *,
    anchor_id: str,
    display: str,
    started: str,
    last_active: str,
    state: str,
    timeline_lines: list[str],
    prefix: str = "",
    suffix: str = "",
) -> None:
    body_parts: list[str] = []
    if prefix.strip():
        body_parts.append(prefix.strip())
    body_parts.append("## Timeline")
    body_parts.extend(timeline_lines)
    if suffix.strip():
        body_parts.append(suffix.strip())

    path.write_text(
        "\n".join(
            [
                "---",
                f'anchor_id: "{anchor_id}"',
                f'display: "{display}"',
                f'started: "{started}"',
                f'last_active: "{last_active}"',
                f'state: "{state}"',
                "---",
                "",
                *body_parts,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_plan(path: Path, *, primary_anchor_id: str, duplicate_anchor_ids: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "primary_anchor_id": primary_anchor_id,
                        "duplicate_anchor_ids": duplicate_anchor_ids,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_obsidian_merge_anchors_dry_run_reports_plan_and_does_not_mutate(tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    daily_dir = vault / "Daily"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)

    _write_anchor(
        anchors_dir / "项目架构 X.md",
        anchor_id="project-architecture-x",
        display="项目架构 X",
        started="2026-05-01",
        last_active="2026-05-09",
        state="active",
        timeline_lines=["- 2026-05-09 架构梳理"],
    )
    _write_anchor(
        anchors_dir / "项目架构 X 副本.md",
        anchor_id="project-architecture-x-copy",
        display="项目架构 X 副本",
        started="2026-05-02",
        last_active="2026-05-10",
        state="candidate",
        timeline_lines=["- 2026-05-10 补充纪要"],
    )
    daily_note = daily_dir / "2026-05-21.md"
    daily_note.write_text("[[项目架构 X 副本|副本]]\n", encoding="utf-8")

    plan_path = tmp_path / "plan.json"
    _write_plan(
        plan_path,
        primary_anchor_id="project-architecture-x",
        duplicate_anchor_ids=["project-architecture-x-copy"],
    )

    result = CliRunner().invoke(
        main,
        [
            "obsidian",
            "merge-anchors",
            "--vault",
            str(vault),
            "--plan",
            str(plan_path),
        ],
    )

    assert result.exit_code == 0
    assert "mode=dry-run" in result.output
    assert "rename_mappings=1" in result.output
    assert "wikilink_rewrites=1" in result.output
    assert (anchors_dir / "项目架构 X 副本.md").exists()
    assert "[[项目架构 X 副本|副本]]" in daily_note.read_text(encoding="utf-8")


def test_obsidian_merge_anchors_apply_merges_timeline_and_rewrites_wikilinks(tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    daily_dir = vault / "Daily"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)

    primary_path = anchors_dir / "项目架构 X.md"
    duplicate_path = anchors_dir / "项目架构 X 纪要.md"

    _write_anchor(
        primary_path,
        anchor_id="project-architecture-x",
        display="项目架构 X",
        started="2026-05-10",
        last_active="2026-05-11",
        state="stale",
        timeline_lines=[
            "- 2026-05-10 架构梳理 → [[2026-05-10]]",
            "- 2026-05-11 方案 A",
        ],
    )
    _write_anchor(
        duplicate_path,
        anchor_id="project-architecture-x-notes",
        display="项目架构 X 纪要",
        started="2026-05-08",
        last_active="2026-05-20",
        state="active",
        timeline_lines=[
            "- 2026-05-08 架构梳理 → [[2026-05-08]]",
            "- 2026-05-10 架构梳理 → [[2026-05-10-copy]]",
            "- 2026-05-11 方案 B",
        ],
    )

    daily_note = daily_dir / "2026-05-21.md"
    daily_note.write_text(
        "[[项目架构 X 纪要]]\n[[项目架构 X 纪要|alias]]\n",
        encoding="utf-8",
    )

    plan_path = tmp_path / "plan.json"
    _write_plan(
        plan_path,
        primary_anchor_id="project-architecture-x",
        duplicate_anchor_ids=["project-architecture-x-notes"],
    )

    result = CliRunner().invoke(
        main,
        [
            "obsidian",
            "merge-anchors",
            "--vault",
            str(vault),
            "--plan",
            str(plan_path),
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert "mode=apply" in result.output
    assert not duplicate_path.exists()

    daily_body = daily_note.read_text(encoding="utf-8")
    assert "[[项目架构 X]]" in daily_body
    assert "项目架构 X 纪要" not in daily_body
    assert "|alias" not in daily_body

    raw_primary = primary_path.read_text(encoding="utf-8")
    frontmatter, body, _ = _split_markdown_frontmatter(raw_primary)
    assert str(frontmatter["started"]) == "2026-05-08"
    assert str(frontmatter["last_active"]) == "2026-05-20"
    assert frontmatter["state"] == "active"
    assert body.count("2026-05-10 架构梳理") == 1
    assert "- 2026-05-11 方案 A" in body
    assert "- 2026-05-11 方案 B" in body


def test_obsidian_merge_anchors_apply_blocks_duplicate_with_extra_content_without_force(tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)

    primary_path = anchors_dir / "主.md"
    duplicate_path = anchors_dir / "副本.md"

    _write_anchor(
        primary_path,
        anchor_id="primary",
        display="主",
        started="2026-05-01",
        last_active="2026-05-01",
        state="active",
        timeline_lines=["- 2026-05-01 主"],
    )
    _write_anchor(
        duplicate_path,
        anchor_id="duplicate",
        display="副本",
        started="2026-05-02",
        last_active="2026-05-02",
        state="candidate",
        timeline_lines=["- 2026-05-02 副本"],
        suffix="## Notes\n手写备注",
    )

    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, primary_anchor_id="primary", duplicate_anchor_ids=["duplicate"])

    result = CliRunner().invoke(
        main,
        [
            "obsidian",
            "merge-anchors",
            "--vault",
            str(vault),
            "--plan",
            str(plan_path),
            "--apply",
        ],
    )

    assert result.exit_code != 0
    assert "rerun with --force" in result.output
    assert duplicate_path.exists()
