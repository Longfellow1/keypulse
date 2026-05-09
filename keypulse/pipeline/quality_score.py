from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from keypulse.pipeline.weekly_validator import ValidationFailure


@dataclass(frozen=True)
class QualityBreakdown:
    structure: int
    coverage: int
    texture: int
    objectivity: int
    antipattern: int
    total: int
    details: list[str]


def _clamp(score: int) -> int:
    return max(0, min(100, score))


def compute_quality_score(failures: list[ValidationFailure]) -> QualityBreakdown:
    structure = 100
    coverage = 100
    texture = 100
    objectivity = 100
    antipattern = 100
    structure_failures = 0
    antipattern_failures = 0
    details: list[str] = []

    for item in failures:
        details.append(f"{item.field}:{item.rule} {item.detail}")
        if item.field == "structure":
            structure -= 20
            structure_failures += 1
        elif item.field == "coverage":
            coverage -= 10
        elif item.field == "texture":
            texture -= 8
        elif item.field == "objectivity":
            objectivity -= 3 if item.rule == "claim_unverified" else 5
        elif item.field == "antipattern":
            antipattern -= 25
            antipattern_failures += 1

    structure = _clamp(structure)
    coverage = _clamp(coverage)
    texture = _clamp(texture)
    objectivity = _clamp(objectivity)
    antipattern = _clamp(antipattern)
    weighted = round((structure + coverage + texture + objectivity + antipattern) / 5)
    total = _clamp(weighted - structure_failures * 16 - antipattern_failures * 20)

    return QualityBreakdown(
        structure=structure,
        coverage=coverage,
        texture=texture,
        objectivity=objectivity,
        antipattern=antipattern,
        total=total,
        details=details,
    )


def append_quality_log(week: str, breakdown: QualityBreakdown, *, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "week": week,
        "total": breakdown.total,
        "breakdown": asdict(breakdown),
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
