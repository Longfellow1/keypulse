from __future__ import annotations

from pathlib import Path

from keypulse.pipeline.weekly_orchestrator import (
    WeeklyRunStats,
    _postprocess_anchor_graph,
    _replace_anchor_display_mentions,
)
from keypulse.pipeline.weekly_topic_anchor import WeeklyAnchor, load_weekly_anchors, save_weekly_anchors


class _Gateway:
    def call(self, capability: str, prompt: str, *, input_data=None, **_kwargs):
        assert capability == "L7_anchor_derivation"
        return {"derived_from": "v2-stable", "confidence": 0.91, "reason": "high_overlap"}


def test_weekly_postprocess_sets_derived_and_converts_wikilink(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg_dir = tmp_path / ".keypulse"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        "\n".join(
            [
                "[obsidian]",
                f'vault_path = "{(tmp_path / "vault").as_posix()}"',
                'vault_name = "KeyPulse"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    anchors = [
        WeeklyAnchor(
            slug="v2-stable",
            display="V2 稳定化",
            started="2026-05-01",
            last_active="2026-05-10",
            state="active",
            timeline_entries=[{"date": "2026-05-10", "summary": "稳定收尾", "daily_ref": "[[2026-05-10]]"}],
        ),
        WeeklyAnchor(
            slug="v3-rollout",
            display="V3 上线",
            started="2026-05-12",
            last_active="2026-05-15",
            state="active",
            timeline_entries=[{"date": "2026-05-15", "summary": "撞到 V2 稳定化 的遗留问题", "daily_ref": "[[2026-05-15]]"}],
        ),
    ]
    save_weekly_anchors("2026-W20", anchors)

    stats = WeeklyRunStats(l5_sources=[])
    updates = _postprocess_anchor_graph("2026-W20", gateway=_Gateway(), stats=stats)

    assert updates["derived_updates"] == 1
    assert updates["wikilink_updates"] >= 1

    loaded = load_weekly_anchors("2026-W20")
    child = next(item for item in loaded if item.slug == "v3-rollout")
    assert child.derived_from == "v2-stable"
    assert "[[V2 稳定化]]" in child.timeline_entries[0]["summary"]

    note = tmp_path / "vault" / "anchors" / "V3 上线.md"
    body = note.read_text(encoding="utf-8")
    assert 'derived_from: "[[V2 稳定化]]"' in body
    assert "[[V2 稳定化]]" in body


def test_replace_anchor_display_mentions_prefers_longest_and_handles_special_chars():
    summary = "先做 V2 稳定化，再看 C++ Runtime (v2)。已有 [[V2 稳定化]] 不重复改。"
    replaced = _replace_anchor_display_mentions(
        summary,
        self_slug="v3-rollout",
        candidates=[
            ("V2", "v2"),
            ("V2 稳定化", "v2-stable"),
            ("C++ Runtime (v2)", "cpp-runtime-v2"),
        ],
    )
    assert "[[V2 稳定化]]" in replaced
    assert "[[C++ Runtime (v2)]]" in replaced
    assert "[[[[V2 稳定化]]]]" not in replaced
