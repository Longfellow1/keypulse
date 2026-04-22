from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_health_report(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write the health report JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
