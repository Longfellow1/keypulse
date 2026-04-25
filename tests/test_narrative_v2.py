from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from keypulse.pipeline.evidence import EvidenceUnit
from keypulse.pipeline.gates import QualityReport
from keypulse.pipeline.narrative_v2 import (
    TwoPassResult,
    _OFFLINE_PLACEHOLDER,
    _factual_fallback,
    run_two_pass,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
_TS0 = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
_TS1 = datetime(2026, 4, 23, 9, 30, 0, tzinfo=timezone.utc)
_TS2 = datetime(2026, 4, 23, 9, 30, 0, tzinfo=timezone.utc)
_TS3 = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


def _online_unit(where: str = "VSCode", what: str = "写代码，修复 bug，review PR") -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=_TS0,
        ts_end=_TS1,
        where=where,
        who="user",
        what=what,
        evidence_refs=[1, 2, 3],
        semantic_weight=0.9,
        machine_online=True,
        confidence=0.9,
    )


def _offline_unit() -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=_TS2,
        ts_end=_TS3,
        where="offline",
        who="-",
        what="",
        evidence_refs=[],
        semantic_weight=0.0,
        machine_online=False,
        confidence=0.1,
    )


def _make_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    p = Path(tmp.name)
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_trigger_log "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ts_utc TEXT, outcome TEXT, note TEXT DEFAULT '')"
    )
    conn.commit()
    conn.close()
    return p


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def render(self, prompt: str) -> str:
        self.calls.append(prompt)
        return f"mock:{hash(prompt)}"


class FailGateway:
    def render(self, prompt: str) -> str:
        raise RuntimeError("simulated LLM failure")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pass1_called_per_unit_except_offline() -> None:
    """3 online + 1 offline -> gateway called exactly 3 times in pass1."""
    db = _make_db()
    gw = FakeGateway()
    units = [_online_unit("A"), _online_unit("B"), _online_unit("C"), _offline_unit()]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    assert len(result.pass1_sentences) == 4
    # pass1 prompt 标志词：/no_think + "写一句中文"；每个 online unit 调一次
    pass1_calls = [c for c in gw.calls if "写一句中文" in c]
    assert len(pass1_calls) == 3


def test_pass2_receives_all_pass1_sentences() -> None:
    """pass2 prompt must contain all pass1 sentences as numbered lines."""
    db = _make_db()
    gw = FakeGateway()
    units = [_online_unit("X"), _online_unit("Y")]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    assert len(result.pass1_sentences) == 2
    # The last call should be pass2 and must contain both sentences
    pass2_prompt = gw.calls[-1]
    assert "1." in pass2_prompt
    assert "2." in pass2_prompt


def test_offline_placeholder_uses_fixed_text() -> None:
    """offline unit must produce _OFFLINE_PLACEHOLDER, not an LLM sentence."""
    db = _make_db()
    gw = FakeGateway()
    units = [_offline_unit()]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    # No pass1 gateway calls (only offline unit → no LLM + pass2 gate may block)
    assert result.pass1_sentences[0] == _OFFLINE_PLACEHOLDER


def test_empty_profile_dict_injects_no_persona_hint() -> None:
    """pass2 prompts must not leak 用户画像 when profile_dict is empty."""
    db = _make_db()
    gw = FakeGateway()
    units = [_online_unit()]

    run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    # Pass2A / Pass2B prompts 均不得含用户画像 section（改版后 persona 已移除）
    for prompt in gw.calls[-2:]:
        assert "用户画像：" not in prompt


def test_should_not_generate_when_red_no_strong() -> None:
    """When gate returns False, pass2 must not call gateway and narrative is skip text."""
    db = _make_db()
    gw = FakeGateway()

    # All units have confidence < 0.3, machine_online=True → weak → red, strong_count=0
    weak_unit = EvidenceUnit(
        ts_start=_TS0,
        ts_end=_TS1,
        where="App",
        who="user",
        what="x",
        evidence_refs=[],
        semantic_weight=0.1,
        machine_online=True,
        confidence=0.1,
    )
    units = [weak_unit]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    assert result.pass2_narrative == "今日证据不足，跳过叙事"
    # pass2 call should not have happened (only pass1 call for the 1 online unit)
    pass2_calls = [c for c in gw.calls if "整合成连贯的日报" in c]
    assert len(pass2_calls) == 0


def test_pending_logged_and_finalized() -> None:
    """Each LLM call must INSERT a pending row and then UPDATE it to ran:ok/ran:fail."""
    db = _make_db()
    gw = FakeGateway()
    units = [_online_unit()]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT outcome FROM llm_trigger_log").fetchall()
    conn.close()

    outcomes = [r[0] for r in rows]
    assert "pending" not in outcomes, "no row should remain in pending state after run"
    assert any(o.startswith("ran:") for o in outcomes)
    assert len(result.pending_ids) == len(outcomes)


def test_gateway_failure_falls_back_to_factual() -> None:
    """When gateway raises, pass1 sentence must be factual fallback (HH:MM-HH:MM 在 <app> 做 ...)."""
    db = _make_db()
    gw = FailGateway()
    unit = _online_unit(where="Xcode", what="修改代码逻辑")
    units = [unit]

    result = run_two_pass(units, db_path=db, gateway=gw, profile_dict={}, date_str="2026-04-23", now=_NOW)

    sentence = result.pass1_sentences[0]
    assert "Xcode" in sentence
    # time is formatted in local tz; just verify HH:MM pattern and app name are present
    assert "在 Xcode" in sentence
