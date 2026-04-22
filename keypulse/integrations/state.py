from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from keypulse.integrations.sinks import SinkTarget


def write_sink_state(path: str | Path, target: SinkTarget) -> None:
    state_path = Path(path).expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(asdict(target), indent=2, default=str))


def read_sink_state(path: str | Path) -> SinkTarget:
    state_path = Path(path).expanduser()
    data = json.loads(state_path.read_text())
    return SinkTarget(
        kind=data["kind"],
        output_dir=Path(data["output_dir"]).expanduser(),
        source=data["source"],
        display_name=data.get("display_name", data["kind"]),
        detected_at=data.get("detected_at", ""),
        metadata=data.get("metadata", {}),
    )
