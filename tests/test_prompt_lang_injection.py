from __future__ import annotations

import keypulse.i18n as i18n
from keypulse.pipeline.daily_orchestrator import _build_prompt as build_daily_orchestrator_prompt
from keypulse.pipeline.daily_strategy import build_prompt as build_daily_strategy_prompt
from keypulse.pipeline.weekly_orchestrator import _build_prompt as build_weekly_prompt


def test_prompt_builders_replace_lang_template(monkeypatch) -> None:
    monkeypatch.setenv("KEYPULSE_LANG", "en")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)

    spec = "Use locale {{lang}} for names"
    payload = {"k": "v"}

    p1 = build_daily_strategy_prompt(spec, "L0_anchor", payload)
    p2 = build_daily_orchestrator_prompt(spec, "L3_topic_naming", payload)
    p3 = build_weekly_prompt(spec, "L4_weekly_reconcile", payload)

    assert "{{lang}}" not in p1
    assert "{{lang}}" not in p2
    assert "{{lang}}" not in p3
    assert "Use locale en for names" in p1
    assert "Use locale en for names" in p2
    assert "Use locale en for names" in p3
