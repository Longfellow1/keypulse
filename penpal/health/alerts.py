from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

AlertLevel = Literal["info", "warn", "critical"]


@dataclass(frozen=True)
class ProductAlert:
    ts: str
    level: AlertLevel
    source: str
    message: str
    suggested_action: str


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
