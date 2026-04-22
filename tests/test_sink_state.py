from __future__ import annotations

from pathlib import Path

from keypulse.integrations.sinks import SinkTarget
from keypulse.integrations.state import read_sink_state, write_sink_state


def test_sink_state_round_trip(tmp_path: Path):
    state_file = tmp_path / "sink-state.json"
    target = SinkTarget(kind="obsidian", output_dir=tmp_path / "Knowledge", source="filesystem")

    write_sink_state(state_file, target)
    loaded = read_sink_state(state_file)

    assert loaded.kind == "obsidian"
    assert loaded.output_dir == target.output_dir
    assert loaded.source == "filesystem"
