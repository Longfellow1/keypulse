from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.paths import get_data_dir

AlertLevel = Literal["info", "warn", "critical"]


@dataclass(frozen=True)
class ProductAlert:
    ts: str
    level: AlertLevel
    source: str
    message: str
    suggested_action: str


def alerts_json_path() -> Path:
    return get_data_dir() / "alerts.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_alert(
    *,
    level: AlertLevel,
    source: str,
    message: str,
    suggested_action: str,
    ts: str | None = None,
) -> ProductAlert:
    return ProductAlert(
        ts=ts or now_iso(),
        level=level,
        source=source.strip(),
        message=message.strip(),
        suggested_action=suggested_action.strip(),
    )


def write_alerts(alerts: list[ProductAlert], *, path: Path | None = None) -> None:
    target = path or alerts_json_path()
    payload = [asdict(item) for item in alerts]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(target, rendered, encoding="utf-8")


def read_alerts(*, path: Path | None = None) -> list[dict[str, str]]:
    target = path or alerts_json_path()
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "").strip()
        if level not in {"info", "warn", "critical"}:
            continue
        out.append(
            {
                "ts": str(item.get("ts") or "").strip(),
                "level": level,
                "source": str(item.get("source") or "").strip(),
                "message": str(item.get("message") or "").strip(),
                "suggested_action": str(item.get("suggested_action") or "").strip(),
            }
        )
    return out
