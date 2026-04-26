from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest

from keypulse.pipeline.evidence import (
    EvidenceUnit,
    enrich_with_evidence,
    extract_evidence_units,
    generate_offline_placeholder,
    load_profile_dict,
    sanitize_unit,
)


# ---------------------------------------------------------------------------
# Minimal WorkBlock stand-in (avoids importing narrative to keep tests isolated)
# ---------------------------------------------------------------------------

@dataclass
class _FakeBlock:
    theme: str
    ts_start: str
    ts_end: str
    primary_app: str = "TestApp"
    event_count: int = 1
    user_candidates: list[dict[str, Any]] = field(default_factory=list)
    system_candidates: list[dict[str, Any]] = field(default_factory=list)
    key_candidates: list[dict[str, Any]] = field(default_factory=list)
    continuity: str = ""
    duration_sec: int = 60
    subtopics: tuple = ()
    session_id: str | None = None
    fragment: bool = False


def _make_db(tmp_path: Path, with_v14: bool = False) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start TEXT NOT NULL,
            ts_end TEXT,
            semantic_weight REAL NOT NULL DEFAULT 0.5
        )
    """)
    if with_v14:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_entities (
                alias TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                kind TEXT,
                weight REAL DEFAULT 1.0
            )
        """)
    conn.commit()
    conn.close()
    return db_path


def _insert_raw_event(db_path: Path, ts: str, weight: float = 0.5):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO raw_events(ts_start, semantic_weight) VALUES (?, ?)",
        (ts, weight),
    )
    conn.commit()
    conn.close()


def _insert_profile_entity(db_path: Path, alias: str, canonical: str):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO profile_entities(alias, canonical_name) VALUES (?, ?)",
        (alias, canonical),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_profile_dict_returns_empty(tmp_path):
    db_path = _make_db(tmp_path, with_v14=True)
    result = load_profile_dict(db_path)
    assert result == {}


def test_extract_unit_from_work_block_basic(tmp_path):
    db_path = _make_db(tmp_path)
    block = _FakeBlock(
        theme="write report",
        ts_start="2026-04-23T09:00:00+00:00",
        ts_end="2026-04-23T09:30:00+00:00",
        primary_app="VSCode",
    )
    _insert_raw_event(db_path, "2026-04-23T09:05:00+00:00", weight=0.9)
    _insert_raw_event(db_path, "2026-04-23T09:10:00+00:00", weight=0.9)
    _insert_raw_event(db_path, "2026-04-23T09:15:00+00:00", weight=0.9)

    def provider(ts_start, ts_end):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id, semantic_weight FROM raw_events WHERE ts_start >= ? AND ts_start <= ?",
            (ts_start, ts_end),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "semantic_weight": r[1]} for r in rows]

    units = extract_evidence_units(block, {}, provider)
    assert len(units) == 1
    unit = units[0]
    assert unit.where == "VSCode"
    assert unit.what == "write report"
    assert unit.who == "user"
    assert unit.machine_online is True
    assert len(unit.evidence_refs) == 3
    assert unit.confidence == 0.9


def test_offline_placeholder_generated_for_gap(tmp_path):
    db_path = _make_db(tmp_path)

    ts1_start = "2026-04-23T08:00:00+00:00"
    ts1_end   = "2026-04-23T08:30:00+00:00"
    ts2_start = "2026-04-23T09:00:00+00:00"  # 30-min gap
    ts2_end   = "2026-04-23T09:30:00+00:00"

    block1 = _FakeBlock(theme="task A", ts_start=ts1_start, ts_end=ts1_end)
    block2 = _FakeBlock(theme="task B", ts_start=ts2_start, ts_end=ts2_end)

    result = enrich_with_evidence([block1, block2], db_path)
    assert len(result) == 2

    # Placeholder should be appended to block1's units
    _, units1 = result[0]
    offline = [u for u in units1 if not u.machine_online]
    assert len(offline) == 1
    assert offline[0].who == "-"
    assert offline[0].confidence == 0.1
    assert offline[0].where == "offline"


def test_no_placeholder_when_raw_events_exist_in_gap(tmp_path):
    db_path = _make_db(tmp_path)

    ts1_end   = "2026-04-23T08:30:00+00:00"
    ts2_start = "2026-04-23T09:00:00+00:00"

    # Insert an event in the gap
    _insert_raw_event(db_path, "2026-04-23T08:45:00+00:00")

    block1 = _FakeBlock(theme="A", ts_start="2026-04-23T08:00:00+00:00", ts_end=ts1_end)
    block2 = _FakeBlock(theme="B", ts_start=ts2_start, ts_end="2026-04-23T09:30:00+00:00")

    result = enrich_with_evidence([block1, block2], db_path)
    _, units1 = result[0]
    offline = [u for u in units1 if not u.machine_online]
    assert len(offline) == 0


def test_confidence_scoring_high_semantic_weight(tmp_path):
    db_path = _make_db(tmp_path)

    # High weight, 3 refs -> 0.9
    block = _FakeBlock(
        theme="design doc",
        ts_start="2026-04-23T10:00:00+00:00",
        ts_end="2026-04-23T10:30:00+00:00",
    )
    for i in range(3):
        _insert_raw_event(db_path, f"2026-04-23T10:0{i}:00+00:00", weight=0.85)

    def provider(ts_start, ts_end):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id, semantic_weight FROM raw_events WHERE ts_start >= ? AND ts_start <= ?",
            (ts_start, ts_end),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "semantic_weight": r[1]} for r in rows]

    units = extract_evidence_units(block, {}, provider)
    assert units[0].confidence == 0.9

    # Low weight, 1 ref -> 0.5
    block2 = _FakeBlock(
        theme="idle",
        ts_start="2026-04-23T11:00:00+00:00",
        ts_end="2026-04-23T11:30:00+00:00",
    )
    _insert_raw_event(db_path, "2026-04-23T11:05:00+00:00", weight=0.6)

    def provider2(ts_start, ts_end):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id, semantic_weight FROM raw_events WHERE ts_start >= ? AND ts_start <= ?",
            (ts_start, ts_end),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "semantic_weight": r[1]} for r in rows]

    units2 = extract_evidence_units(block2, {}, provider2)
    assert units2[0].confidence == 0.5


def test_profile_alias_merging(tmp_path):
    db_path = _make_db(tmp_path, with_v14=True)
    _insert_profile_entity(db_path, "Alice", "Alice Chen")
    _insert_profile_entity(db_path, "proj-foo", "Project Foo")

    profile = load_profile_dict(db_path)
    assert profile["Alice"] == "Alice Chen"
    assert profile["proj-foo"] == "Project Foo"

    block = _FakeBlock(
        theme="review Alice spec for proj-foo",
        ts_start="2026-04-23T14:00:00+00:00",
        ts_end="2026-04-23T14:30:00+00:00",
        primary_app="Notion",
    )

    units = extract_evidence_units(block, profile, lambda _s, _e: [])
    assert units[0].what == "review Alice Chen spec for Project Foo"


# ---------------------------------------------------------------------------
# sanitize_unit: privacy filter
# ---------------------------------------------------------------------------

def _unit(*, where: str = "Notion", what: str = "review spec") -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        ts_end=datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc),
        where=where,
        who="user",
        what=what,
        evidence_refs=[1],
        semantic_weight=0.6,
        machine_online=True,
        confidence=0.7,
    )


def test_sanitize_keeps_normal_unit():
    assert sanitize_unit(_unit()) is not None


def test_sanitize_drops_keychain_and_1password():
    assert sanitize_unit(_unit(where="Keychain Access")) is None
    assert sanitize_unit(_unit(where="1Password 7")) is None


def test_sanitize_drops_when_what_contains_credential_keywords():
    for what in ("my password is hunter2", "验证码 8421", "OTP 932118", "2FA backup"):
        assert sanitize_unit(_unit(what=what)) is None, what


def test_sanitize_keeps_loginwindow_misclassified_real_work():
    """macOS bug: active_app gets stuck on 'loginwindow' during lock/unlock,
    but the captured `what` is real work content. Don't drop on app name alone."""
    unit = _unit(where="loginwindow", what="先解决输入卫生（高优）—— S3 接线时在 fragments → slices")
    assert sanitize_unit(unit) is not None


# ---------------------------------------------------------------------------
# extract_evidence_units: loginwindow misclassification override
# ---------------------------------------------------------------------------

def test_extract_overrides_loginwindow_when_theme_is_real_work(tmp_path):
    """macOS bug: primary_app gets reported as 'loginwindow' during lock/unlock,
    but the captured theme is actual work content. We replace where with
    '(unknown)' so the LLM does not literalize 'loginwindow' into the narrative."""
    db_path = _make_db(tmp_path)
    block = _FakeBlock(
        theme="先解决输入卫生（高优）—— S3 接线时在 fragments → slices",
        ts_start="2026-04-25T09:29:00+00:00",
        ts_end="2026-04-25T09:35:00+00:00",
        primary_app="loginwindow",
    )
    units = extract_evidence_units(block, {}, lambda _s, _e: [])
    assert units[0].where == "(unknown)"


def test_extract_keeps_loginwindow_when_theme_is_short(tmp_path):
    """Short/empty content with primary_app=loginwindow stays as loginwindow.
    Capture-layer L3 filter (fragments.py) should already drop these upstream;
    we don't second-guess it here."""
    db_path = _make_db(tmp_path)
    block = _FakeBlock(
        theme="login",
        ts_start="2026-04-25T09:29:00+00:00",
        ts_end="2026-04-25T09:35:00+00:00",
        primary_app="loginwindow",
    )
    units = extract_evidence_units(block, {}, lambda _s, _e: [])
    assert units[0].where == "loginwindow"


def test_extract_overrides_other_overlays_too(tmp_path):
    """Same override applies to Dock / Notification Center / ScreenSaverEngine."""
    db_path = _make_db(tmp_path)
    for overlay in ("Dock", "Notification Center", "ScreenSaverEngine"):
        block = _FakeBlock(
            theme="long enough work content describing what the user actually did",
            ts_start="2026-04-25T09:29:00+00:00",
            ts_end="2026-04-25T09:35:00+00:00",
            primary_app=overlay,
        )
        units = extract_evidence_units(block, {}, lambda _s, _e: [])
        assert units[0].where == "(unknown)", f"{overlay} should be overridden"


def test_extract_does_not_override_normal_app(tmp_path):
    db_path = _make_db(tmp_path)
    block = _FakeBlock(
        theme="long enough work content describing real activity here",
        ts_start="2026-04-25T09:29:00+00:00",
        ts_end="2026-04-25T09:35:00+00:00",
        primary_app="VSCode",
    )
    units = extract_evidence_units(block, {}, lambda _s, _e: [])
    assert units[0].where == "VSCode"
