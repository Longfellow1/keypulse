from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEALTH_JSON_PATH = Path("~/.keypulse/health.json").expanduser()


def write_health_report(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write the health report JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def read_health_report(path: Path = HEALTH_JSON_PATH) -> dict[str, Any]:
    """Best-effort read of the health report. Returns {} on any failure."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
