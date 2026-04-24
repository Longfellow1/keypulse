from __future__ import annotations

from dataclasses import dataclass, field

from keypulse.pipeline.evidence import EvidenceUnit


@dataclass
class QualityReport:
    total_units: int
    strong_count: int       # confidence >= 0.7
    moderate_count: int     # 0.4 <= confidence < 0.7
    weak_count: int         # confidence < 0.4 and machine_online=True
    offline_count: int      # machine_online=False
    strong_ratio: float     # strong / (total - offline); 0.0 when denominator is 0
    overall: str            # "green" / "yellow" / "red"
    reasons: list[str] = field(default_factory=list)


def score_field_completeness(unit: EvidenceUnit) -> float:
    """Four-field completeness: time, place, person, event. Each worth 0.25.

    - ts_start and ts_end -> time 0.25
    - where and where != "" and where != "-" -> place 0.25
    - who and who not in ("", "-") -> person 0.25
    - what and len(what.strip()) >= 10 -> event 0.25

    Returns 0.0-1.0.
    """
    score = 0.0

    if unit.ts_start and unit.ts_end:
        score += 0.25

    if unit.where and unit.where not in ("", "-"):
        score += 0.25

    if unit.who and unit.who not in ("", "-"):
        score += 0.25

    if unit.what and len(unit.what.strip()) >= 10:
        score += 0.25

    return score


def partition_units(units: list[EvidenceUnit]) -> dict[str, list[EvidenceUnit]]:
    """Partition units by confidence and machine_online into {strong, moderate, weak, offline}."""
    result: dict[str, list[EvidenceUnit]] = {
        "strong": [],
        "moderate": [],
        "weak": [],
        "offline": [],
    }
    for unit in units:
        if not unit.machine_online:
            result["offline"].append(unit)
        elif unit.confidence >= 0.7:
            result["strong"].append(unit)
        elif unit.confidence >= 0.4:
            result["moderate"].append(unit)
        else:
            result["weak"].append(unit)
    return result


def summarize_quality(units: list[EvidenceUnit]) -> QualityReport:
    """Generate an overall quality report for a list of EvidenceUnits.

    overall thresholds:
      - green:  strong_ratio >= 0.6 and weak_count <= 2
      - red:    strong_ratio < 0.3 or weak_count > total / 2
      - else:   yellow
    """
    partitions = partition_units(units)
    total = len(units)
    strong_count = len(partitions["strong"])
    moderate_count = len(partitions["moderate"])
    weak_count = len(partitions["weak"])
    offline_count = len(partitions["offline"])

    online_total = total - offline_count
    strong_ratio = strong_count / online_total if online_total > 0 else 0.0

    reasons: list[str] = []

    if total == 0:
        reasons.append("no evidence today")
        overall = "red"
        return QualityReport(
            total_units=total,
            strong_count=strong_count,
            moderate_count=moderate_count,
            weak_count=weak_count,
            offline_count=offline_count,
            strong_ratio=strong_ratio,
            overall=overall,
            reasons=reasons,
        )

    if weak_count > 0:
        reasons.append(f"{weak_count} weak evidence units")

    if offline_count > 0:
        reasons.append(f"{offline_count} offline periods")

    if strong_ratio >= 0.6 and weak_count <= 2:
        overall = "green"
    elif strong_ratio < 0.3 or weak_count > total / 2:
        overall = "red"
    else:
        overall = "yellow"

    return QualityReport(
        total_units=total,
        strong_count=strong_count,
        moderate_count=moderate_count,
        weak_count=weak_count,
        offline_count=offline_count,
        strong_ratio=strong_ratio,
        overall=overall,
        reasons=reasons,
    )


def should_generate_narrative(
    report: QualityReport, *, min_total: int = 1
) -> tuple[bool, str]:
    """Gate check for narrative generation (used by MVP-4).

    - total < min_total -> (False, 'quality:no_evidence')
    - overall == 'red' and strong_count == 0 -> (False, 'quality:red_no_strong')
    - otherwise -> (True, 'quality:ok')
    """
    if report.total_units < min_total:
        return (False, "quality:no_evidence")
    if report.overall == "red" and report.strong_count == 0:
        return (False, "quality:red_no_strong")
    return (True, "quality:ok")
