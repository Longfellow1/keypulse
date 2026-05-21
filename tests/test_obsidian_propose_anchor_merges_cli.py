from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import (
    _collect_anchor_merge_candidates,
    _normalize_anchor_merge_groups,
    main,
)


def _write_anchor(
    path: Path,
    *,
    anchor_id: str,
    display: str,
    started: str,
    last_active: str,
    state: str,
    timeline: str,
) -> None:
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
                "## Timeline",
                timeline,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_collect_anchor_merge_candidates_extracts_frontmatter_and_timeline_excerpt(tmp_path: Path) -> None:
    anchors_dir = tmp_path / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    long_timeline = "- 2026-05-01 " + ("多轮训练数据范式修正 " * 30)
    _write_anchor(
        anchors_dir / "a.md",
        anchor_id="train-data-paradigm",
        display="多轮训练数据范式问题排查",
        started="2026-05-01",
        last_active="2026-05-09",
        state="active",
        timeline=long_timeline,
    )

    rows = _collect_anchor_merge_candidates(anchors_dir, max_anchors=100)

    assert len(rows) == 1
    row = rows[0]
    assert row["anchor_id"] == "train-data-paradigm"
    assert row["display"] == "多轮训练数据范式问题排查"
    assert row["started"] == "2026-05-01"
    assert row["last_active"] == "2026-05-09"
    assert row["state"] == "active"
    assert len(row["timeline_excerpt"]) <= 200
    assert "多轮训练数据范式修正" in row["timeline_excerpt"]


def test_normalize_anchor_merge_groups_success_and_invalid() -> None:
    known_ids = {"a", "b", "c"}
    parsed = _normalize_anchor_merge_groups(
        {
            "groups": [
                {
                    "primary_anchor_id": "a",
                    "duplicates": ["b", "a", "x"],
                    "reason": "同一长尾",
                }
            ]
        },
        known_ids,
    )
    assert parsed == [
        {
            "primary_anchor_id": "a",
            "duplicates": ["b"],
            "reason": "同一长尾",
            "group_title": "",
        }
    ]

    try:
        _normalize_anchor_merge_groups({"groups": [{"duplicates": ["b"], "reason": "x"}]}, known_ids)
    except ValueError as exc:
        assert "primary_anchor_id" in str(exc)
    else:
        raise AssertionError("expected ValueError for malformed group")


def test_obsidian_propose_anchor_merges_outputs_markdown_with_mock_llm(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    _write_anchor(
        anchors_dir / "a.md",
        anchor_id="train-data-paradigm",
        display="多轮训练数据范式问题排查",
        started="2026-05-06",
        last_active="2026-05-09",
        state="active",
        timeline="- 2026-05-06 排查",
    )
    _write_anchor(
        anchors_dir / "b.md",
        anchor_id="train-data-paradigm-fix",
        display="多轮训练数据范式修正",
        started="2026-05-07",
        last_active="2026-05-10",
        state="candidate",
        timeline="- 2026-05-07 修正",
    )
    _write_anchor(
        anchors_dir / "c.md",
        anchor_id="project-x",
        display="项目架构 X",
        started="2026-05-08",
        last_active="2026-05-11",
        state="active",
        timeline="- 2026-05-08 讨论",
    )

    class FakeGateway:
        def call(self, capability, prompt, **kwargs):
            del capability, prompt, kwargs
            return {
                "groups": [
                    {
                        "group_title": "多轮训练数据范式",
                        "primary_anchor_id": "train-data-paradigm",
                        "duplicates": ["train-data-paradigm-fix"],
                        "reason": "同一长尾工作的连续多天进展",
                    }
                ]
            }

    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda _cfg: FakeGateway())

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "propose-anchor-merges", "--vault", str(vault)])

    assert result.exit_code == 0
    assert "# 建议合并组（1 个）" in result.output
    assert "## 组 1: 多轮训练数据范式" in result.output
    assert "主 anchor: 多轮训练数据范式问题排查 (started 2026-05-06, active)" in result.output
    assert "  - 多轮训练数据范式修正 (started 2026-05-07)" in result.output
    assert "理由: 同一长尾工作的连续多天进展" in result.output


def test_obsidian_propose_anchor_merges_reports_parse_error(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    anchors_dir = vault / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    _write_anchor(
        anchors_dir / "a.md",
        anchor_id="a",
        display="A",
        started="2026-05-01",
        last_active="2026-05-02",
        state="active",
        timeline="- t",
    )

    class BadGateway:
        def call(self, capability, prompt, **kwargs):
            del capability, prompt, kwargs
            return {"groups": [{"duplicates": ["a"], "reason": "bad"}]}

    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda _cfg: BadGateway())

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "propose-anchor-merges", "--vault", str(vault)])

    assert result.exit_code != 0
    assert "LLM merge proposal parse failed" in result.output
