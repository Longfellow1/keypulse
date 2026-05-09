from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from keypulse.pipeline.low_activity import is_low_activity_week
from keypulse.pipeline.onboarding import (
    OnboardingAnswers,
    is_first_run,
    read_profile,
    validate_answers,
    write_profile,
)
from keypulse.pipeline.rollup_orchestrator import run_rollup


def test_validate_answers_missing_and_valid():
    missing_errors = validate_answers({"work_type": "developer"})
    assert any("missing field" in err for err in missing_errors)

    valid = {
        "work_type": "developer",
        "region": "CN",
        "work_mode": "flexible",
        "weekly_trigger": "fri-pm",
        "weekly_style": "exec",
        "religion": "unspecified",
        "created_at": "2026-05-09T10:00:00Z",
    }
    assert validate_answers(valid) == []


def test_profile_write_read_roundtrip(tmp_path):
    profile = tmp_path / "profile.toml"
    answers = OnboardingAnswers(
        work_type="developer",
        region="CN",
        work_mode="flexible",
        weekly_trigger="fri-pm",
        weekly_style="exec",
        religion="",
        created_at="2026-05-09T10:00:00Z",
    )
    write_profile(answers, profile)
    loaded = read_profile(profile)
    assert loaded == answers


def test_is_first_run(tmp_path):
    profile = tmp_path / "profile.toml"
    assert is_first_run(profile) is True
    profile.write_text("[user]\nwork_type='developer'\n", encoding="utf-8")
    assert is_first_run(profile) is False


def test_rollup_week_delegates(monkeypatch):
    def fake_run_weekly(week: str, *, style: str = "plain") -> str:
        assert week == "2026-W19"
        assert style == "exec"
        return "/tmp/weekly.md"

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)
    assert run_rollup("week", "2026-W19", style="exec") == "/tmp/weekly.md"


def test_rollup_month_not_implemented():
    with pytest.raises(NotImplementedError):
        run_rollup("month", "2026-05")


def test_is_low_activity_week_cases():
    assert is_low_activity_week([], historical_avg=10) is False
    assert is_low_activity_week([1, 2, 2, 1], historical_avg=10) is True
    assert is_low_activity_week([1, 2, 10, 1, 2], historical_avg=10) is False
    assert is_low_activity_week([0, 0, 0], historical_avg=0) is False


def test_holidays_yaml_parse_and_coverage():
    holidays_path = Path(__file__).resolve().parents[1] / "keypulse" / "data" / "holidays.yaml"
    parsed = yaml.safe_load(holidays_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    holidays = parsed.get("holidays")
    assert isinstance(holidays, list)
    assert len(holidays) >= 50
    names = {item.get("name") for item in holidays if isinstance(item, dict)}
    assert "春节" in names
    assert "Thanksgiving" in names
    assert "Holi" in names
    valid_types = {"long_holiday", "single_day_legal", "cultural", "religious"}
    assert all(isinstance(item, dict) and item.get("type") in valid_types for item in holidays)


def test_weekly_metadata_has_user_annotation_key():
    weekly_path = Path(__file__).resolve().parents[1] / "keypulse" / "pipeline" / "weekly_orchestrator.py"
    source = weekly_path.read_text(encoding="utf-8")
    assert '"weekly_last_outcome"' in source
    assert '"user_annotation"' in source
