from __future__ import annotations

from keypulse.config import Config


def test_llm_config_defaults():
    cfg = Config.model_validate({})

    assert cfg.llm.tier == "mini"
    assert cfg.llm.monthly_budget_usd == 5.0
    assert cfg.llm.local_ollama_url == "http://localhost:11434"


def test_llm_config_accepts_tiers():
    cfg = Config.model_validate({"llm": {"tier": "premium", "provider": "anthropic"}})

    assert cfg.llm.tier == "premium"
    assert cfg.llm.provider == "anthropic"
