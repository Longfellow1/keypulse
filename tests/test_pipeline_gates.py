"""Tests for keypulse.pipeline.gates (MVP-3 quality gate)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from keypulse.pipeline.evidence import EvidenceUnit
from keypulse.pipeline.gates import (
    QualityReport,
    partition_units,
    score_field_completeness,
    should_generate_narrative,
    summarize_quality,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
_TS2 = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


def _unit(
    *,
    confidence: float = 0.9,
    machine_online: bool = True,
    where: str = "Xcode",
    who: str = "alice",
    what: str = "Implemented the login feature for the app",
    evidence_refs: list[int] | None = None,
    semantic_weight: float = 0.8,
) -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=_TS,
        ts_end=_TS2,
        where=where,
        who=who,
        what=what,
        evidence_refs=evidence_refs if evidence_refs is not None else [1, 2, 3],
        semantic_weight=semantic_weight,
        machine_online=machine_online,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# score_field_completeness
# ---------------------------------------------------------------------------


def test_score_completeness_full_unit():
    unit = _unit()
    assert score_field_completeness(unit) == 1.0


def test_score_completeness_missing_what():
    unit = _unit(what="")
    assert score_field_completeness(unit) == 0.75


def test_score_completeness_offline_placeholder():
    # Mirrors generate_offline_placeholder output: where="offline", who="-", what=""
    unit = EvidenceUnit(
        ts_start=_TS,
        ts_end=_TS2,
        where="-",
        who="-",
        what="machine offline",  # <10 chars triggers event=0
        evidence_refs=[],
        semantic_weight=0.0,
        machine_online=False,
        confidence=0.1,
    )
    # ts_start+ts_end -> 0.25; where="-" -> 0; who="-" -> 0; what="machine offline" (14 chars) -> 0.25
    assert score_field_completeness(unit) == 0.5


def test_score_completeness_what_short():
    # what has content but less than 10 chars after strip
    unit = _unit(what="short")
    assert score_field_completeness(unit) == 0.75


# ---------------------------------------------------------------------------
# partition_units
# ---------------------------------------------------------------------------


def test_partition_by_confidence():
    units = [
        _unit(confidence=0.9),   # strong
        _unit(confidence=0.8),   # strong
        _unit(confidence=0.7),   # strong (boundary, >= 0.7)
        _unit(confidence=0.5),   # moderate
        _unit(confidence=0.4),   # moderate (boundary, >= 0.4)
        _unit(confidence=0.2),   # weak (online but < 0.4)
        _unit(confidence=0.1, machine_online=False),  # offline
    ]
    result = partition_units(units)
    assert len(result["strong"]) == 3
    assert len(result["moderate"]) == 2
    assert len(result["weak"]) == 1
    assert len(result["offline"]) == 1


# ---------------------------------------------------------------------------
# summarize_quality
# ---------------------------------------------------------------------------


def test_summarize_green_path():
    units = [_unit(confidence=0.9)] * 5 + [_unit(confidence=0.5)]
    report = summarize_quality(units)
    assert report.overall == "green"
    assert report.strong_count == 5
    assert report.moderate_count == 1
    assert report.weak_count == 0
    assert report.offline_count == 0
    assert report.total_units == 6
    assert report.strong_ratio == pytest.approx(5 / 6)


def test_summarize_red_path():
    # 1 strong + 4 weak -> strong_ratio = 1/5 = 0.2 < 0.3 (red), weak_count=4 > 5/2
    units = [_unit(confidence=0.9)] + [_unit(confidence=0.2)] * 4
    report = summarize_quality(units)
    assert report.overall == "red"
    assert report.strong_count == 1
    assert report.weak_count == 4
    assert any("weak" in r for r in report.reasons)


def test_summarize_yellow_path():
    # 3 strong + 3 moderate: ratio=0.5 (< 0.6) -> yellow; weak=0 so not red
    units = [_unit(confidence=0.9)] * 3 + [_unit(confidence=0.5)] * 3
    report = summarize_quality(units)
    assert report.overall == "yellow"


def test_summarize_empty_units():
    report = summarize_quality([])
    assert report.total_units == 0
    assert report.overall == "red"
    assert any("no evidence" in r for r in report.reasons)
    assert report.strong_ratio == 0.0


# ---------------------------------------------------------------------------
# should_generate_narrative
# ---------------------------------------------------------------------------


def test_should_generate_narrative_no_evidence():
    report = summarize_quality([])
    ok, reason = should_generate_narrative(report)
    assert ok is False
    assert reason == "quality:no_evidence"


def test_should_generate_narrative_red_no_strong():
    # All weak units -> red, strong_count=0
    units = [_unit(confidence=0.2)] * 5
    report = summarize_quality(units)
    assert report.overall == "red"
    assert report.strong_count == 0
    ok, reason = should_generate_narrative(report)
    assert ok is False
    assert reason == "quality:red_no_strong"


def test_should_generate_narrative_yellow_ok():
    # yellow but has strong units -> should proceed
    units = [_unit(confidence=0.9)] * 3 + [_unit(confidence=0.5)] * 3
    report = summarize_quality(units)
    assert report.overall == "yellow"
    assert report.strong_count == 3
    ok, reason = should_generate_narrative(report)
    assert ok is True
    assert reason == "quality:ok"


def test_should_generate_narrative_green_ok():
    units = [_unit(confidence=0.9)] * 5 + [_unit(confidence=0.5)]
    report = summarize_quality(units)
    ok, reason = should_generate_narrative(report)
    assert ok is True
    assert reason == "quality:ok"
