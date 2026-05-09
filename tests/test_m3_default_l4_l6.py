from __future__ import annotations

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline import weekly_orchestrator
from keypulse.pipeline.rollup_orchestrator import run_rollup
from keypulse.pipeline.weekly_validator import validate_weekly_output


def test_cli_weekly_default_style_exec(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run_weekly(week_str: str, *, style: str = "exec") -> str:
        captured["week"] = week_str
        captured["style"] = style
        return "/tmp/weekly.md"

    monkeypatch.setattr("keypulse.cli.run_weekly", fake_run_weekly)
    monkeypatch.setattr("keypulse.cli.get_config", lambda: object())
    monkeypatch.setattr("keypulse.cli.require_db", lambda _cfg: None)
    monkeypatch.setattr("keypulse.cli.get_state", lambda _key: "")

    result = CliRunner().invoke(main, ["weekly", "run", "--week", "2026-W19"])
    assert result.exit_code == 0
    assert captured == {"week": "2026-W19", "style": "exec"}


def test_run_weekly_default_style_exec() -> None:
    assert weekly_orchestrator.run_weekly.__kwdefaults__ == {"style": "exec"}


def test_run_rollup_default_style_exec(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run_weekly(week_str: str, *, style: str = "exec") -> str:
        captured["week"] = week_str
        captured["style"] = style
        return "/tmp/weekly.md"

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)
    assert run_rollup("week", "2026-W19") == "/tmp/weekly.md"
    assert captured == {"week": "2026-W19", "style": "exec"}


def test_parse_llm_json_robust_cases() -> None:
    assert weekly_orchestrator._parse_llm_json_robust('{"a": 1}') == {"a": 1}
    assert weekly_orchestrator._parse_llm_json_robust("```json\n{\"a\":1}\n```") == {"a": 1}
    assert weekly_orchestrator._parse_llm_json_robust("prefix text\n{\"a\": 1}\ntrailing note") == {"a": 1}
    assert weekly_orchestrator._parse_llm_json_robust('{"a": 1,}') == {"a": 1}
    assert weekly_orchestrator._parse_llm_json_robust("not-json-at-all") is None


def test_l6_cache_key_is_stable() -> None:
    topics_a = [{"slug": "beta"}, {"slug": "alpha"}]
    topics_b = [{"slug": "alpha"}, {"slug": "beta"}]
    profile = {"work_type": "developer", "region": "CN", "religion": ""}
    corpus = "2026-05-04 did A\n2026-05-05 did B"

    key1 = weekly_orchestrator._stable_l6_cache_key("2026-W19", topics_a, corpus, profile)
    key2 = weekly_orchestrator._stable_l6_cache_key("2026-W19", topics_b, corpus, profile)
    key3 = weekly_orchestrator._stable_l6_cache_key("2026-W19", topics_a, corpus + "\nmore", profile)

    assert key1 == key2
    assert key1 != key3


def test_l6_cache_hit_keeps_dropped_balls(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    weekly_orchestrator._WEEKLY_MEMORY_CACHE.clear()
    l6_output = {
        "dropped_balls": [{"what": "follow-up missing", "anchor_link": "[[2026-05-08]]"}],
        "missed_balls": [],
        "observation": {"text": "observe?", "anchor_link": "[[2026-05-08]]", "anchor_quote": "q"},
        "risks": [{"risk": "r", "impact": "i", "direction": "d"}],
    }
    weekly_orchestrator._weekly_cache_put("2026-W19", "L6", "stable-key", l6_output)
    cached = weekly_orchestrator._weekly_cache_get("2026-W19", "L6", "stable-key")
    normalized = weekly_orchestrator._normalize_l6_output(cached)
    assert normalized["dropped_balls"] == l6_output["dropped_balls"]


def test_w19_plain_exec_golden_regression_zero_failures() -> None:
    plain = open("docs/golden-weekly/2026-W19-plain.md", "r", encoding="utf-8").read()
    exec_md = open("docs/golden-weekly/2026-W19-exec.md", "r", encoding="utf-8").read()
    hud_dates = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"]
    plain_failures = validate_weekly_output(style="plain", rendered_markdown=plain, dailies_corpus=plain, hud_input_dates=hud_dates)
    exec_failures = validate_weekly_output(style="exec", rendered_markdown=exec_md, dailies_corpus=exec_md, hud_input_dates=hud_dates)
    assert plain_failures == []
    assert exec_failures == []
