from __future__ import annotations

from keypulse.pipeline.aggregate import build_theme_summary
from keypulse.pipeline.themes import record_theme_refine, read_theme_profile, write_theme_profile


def test_theme_refine_increments_version_and_affects_aggregate_summary(tmp_path):
    state_path = tmp_path / "theme-state.json"
    write_theme_profile(state_path, theme_name="decision-making", version=1, instructions=["keep it short"])

    updated = record_theme_refine(state_path, theme_name="decision-making", instruction="use tighter language")
    profile = read_theme_profile(state_path)
    summary = build_theme_summary(
        [{"topic": "decision-making", "title": "Better selection rules"}],
        theme_state_path=state_path,
    )

    assert updated.version == 2
    assert profile.version == 2
    assert "decision-making v2" in summary.body
