from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from keypulse.config import Config
from keypulse.pipeline.model import LLMCallError, ModelGateway


def _cfg(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "llm": {
                "tier": "mini",
                "provider": "",
                "monthly_budget_usd": 5.0,
                "local_ollama_url": "http://localhost:11434",
            },
            "model": {
                "active_profile": "local-first",
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "local-model",
                },
            },
        }
    )


class _OutputSchema(BaseModel):
    ok: str


def test_call_cache_miss_then_hit_writes_cost(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))

    calls: list[int] = []

    def fake_call(**kwargs):
        calls.append(1)
        return {
            "text": "cached text",
            "in_tokens": 120,
            "out_tokens": 20,
            "cost_usd": 0.001,
        }

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    first = gateway.call("cache_test", "hello", prompt_version="L1.v1", input_data={"a": 1})
    second = gateway.call("cache_test", "hello", prompt_version="L1.v1", input_data={"a": 1})

    assert first == "cached text"
    assert second == "cached text"
    assert len(calls) == 1

    cost_path = tmp_path / ".keypulse" / "cost.jsonl"
    rows = [json.loads(line) for line in cost_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["cache_hit"] is False
    assert rows[1]["cache_hit"] is True
    assert rows[1]["cost_usd"] == 0.0


def test_call_cache_ttl_expired_forces_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))

    counter = {"n": 0}

    def fake_call(**kwargs):
        counter["n"] += 1
        return {
            "text": f"text-{counter['n']}",
            "in_tokens": 10,
            "out_tokens": 5,
            "cost_usd": 0.0001,
        }

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    gateway.call("cache_test", "hello", prompt_version="L1.v1")

    cache_dir = tmp_path / ".keypulse" / "cache" / "llm"
    cache_file = next(cache_dir.glob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    payload["ts"] = old_ts
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = gateway.call("cache_test", "hello", prompt_version="L1.v1")

    assert out == "text-2"
    assert counter["n"] == 2


def test_call_schema_validation_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))

    responses = iter(
        [
            {"text": '{"bad":"x"}', "in_tokens": 10, "out_tokens": 2, "cost_usd": 0.0001},
            {"text": '{"ok":"yes"}', "in_tokens": 10, "out_tokens": 2, "cost_usd": 0.0001},
        ]
    )

    def fake_call(**kwargs):
        return next(responses)

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    result = gateway.call("cache_test", "hello", schema=_OutputSchema, prompt_version="L1.v1")

    assert isinstance(result, _OutputSchema)
    assert result.ok == "yes"


def test_call_retry_exhausted_raises_llm_call_error(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))

    def always_fail(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(gateway, "_call_capability_backend", always_fail)

    with pytest.raises(LLMCallError, match="capability=cache_test"):
        gateway.call("cache_test", "hello", prompt_version="L1.v1")


def test_strip_json_fence_handles_common_wrappings():
    from keypulse.pipeline.model import _strip_json_fence

    assert _strip_json_fence('{"a": 1}') == '{"a": 1}'
    assert _strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json_fence('  ```json\n{"a": 1}\n```  ') == '{"a": 1}'
    # Truncated response — no closing fence — should still strip the opener.
    assert _strip_json_fence('```json\n{"a": 1}') == '{"a": 1}'


def test_schema_validate_unwraps_fenced_json(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))
    schema = {"type": "object", "required": ["markdown"], "properties": {"markdown": {"type": "string"}}}
    fenced = '```json\n{"markdown": "📍 hello"}\n```'

    parsed = gateway._schema_validate(schema, fenced)

    assert parsed == {"markdown": "📍 hello"}
