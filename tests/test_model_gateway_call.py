from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from keypulse.config import Config
from keypulse.pipeline.model import LLMCallError, ModelBackend, ModelGateway, NoBackendAvailable


def _cfg(tmp_path: Path, *, profile: str = "local-first", cloud_model: str = "") -> Config:
    cloud = (
        {
            "kind": "openai_compatible",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": cloud_model,
            "api_key_env": "ARK_API_KEY",
        }
        if cloud_model
        else {"kind": "disabled", "base_url": "", "model": ""}
    )
    return Config.model_validate(
        {
            "llm": {
                "tier": "mini",
                "provider": "",
                "monthly_budget_usd": 5.0,
                "local_ollama_url": "http://localhost:11434",
            },
            "model": {
                "active_profile": profile,
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "local-model",
                },
                "cloud": cloud,
            },
        }
    )


class _OutputSchema(BaseModel):
    ok: str


def test_call_cache_miss_then_hit_writes_cost_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))

    calls: list[int] = []

    def fake_call(**kwargs):
        assert kwargs["backend"].model == "local-model"
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
    rows = [json.loads(line) for line in cost_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["capability"] == "cache_test"
    assert rows[0]["model"] == "local/local-model"
    assert rows[0]["in_tokens"] == 120
    assert rows[0]["out_tokens"] == 20
    assert rows[0]["cost_usd"] == 0.001
    assert rows[0]["cache_hit"] is False
    assert rows[0]["prompt_version"] == "L1.v1"
    assert rows[1]["cache_hit"] is True
    assert rows[1]["in_tokens"] == 120
    assert rows[1]["out_tokens"] == 20
    assert rows[1]["cost_usd"] == 0.0


def test_call_uses_configured_cloud_backend_when_profile_selects_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-first", cloud_model="doubao-seed-1-6"))

    seen: dict[str, str] = {}

    def fake_call(**kwargs):
        backend = kwargs["backend"]
        seen["kind"] = backend.kind
        seen["base_url"] = backend.base_url
        seen["model"] = backend.model
        return {"text": "cloud text", "in_tokens": 10, "out_tokens": 2, "cost_usd": 123.0}

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    assert gateway.call("cache_test", "hello", prompt_version="L1.v1") == "cloud text"
    assert seen == {
        "kind": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6",
    }

    cache_file = next((tmp_path / ".keypulse" / "cache" / "llm").glob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["model"] == "cloud/doubao-seed-1-6"


def test_call_can_select_configured_anthropic_backend(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "cloud-first",
                "state_path": str(tmp_path / "model-state.json"),
                "cloud": {
                    "kind": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
                "local": {"kind": "disabled", "base_url": "", "model": ""},
            }
        }
    )
    gateway = ModelGateway(cfg)

    def fake_call(**kwargs):
        backend = kwargs["backend"]
        return {"text": f"{backend.kind}:{backend.model}", "in_tokens": 1, "out_tokens": 1, "cost_usd": 999.0}

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    assert gateway.call("cache_test", "hello", prompt_version="L1.v1") == "anthropic:claude-sonnet-4-6"


def test_call_skips_short_circuited_cloud_and_uses_local(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-first", cloud_model="doubao-seed-1-6"))
    gateway._short_circuit("cloud", minutes=30, reason="seed")

    seen: dict[str, str] = {}

    def fake_call(**kwargs):
        backend = kwargs["backend"]
        seen["model"] = backend.model
        return {"text": "local text", "in_tokens": 10, "out_tokens": 2, "cost_usd": 0.0}

    monkeypatch.setattr(gateway, "_call_capability_backend", fake_call)

    assert gateway.call("cache_test", "hello", prompt_version="L1.v1") == "local text"
    assert seen["model"] == "local-model"


def test_call_raises_when_selected_backend_has_empty_model(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-only", cloud_model=""))
    monkeypatch.setattr(gateway, "select_backend", lambda stage="write": ModelBackend(kind="disabled", base_url="", model=""))

    with pytest.raises(NoBackendAvailable):
        gateway.call("cache_test", "hello", prompt_version="L1.v1")


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

    cost_path = tmp_path / ".keypulse" / "cost.jsonl"
    rows = [json.loads(line) for line in cost_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["capability"] == "cache_test"
    assert rows[0]["cache_hit"] is False
    assert rows[0]["in_tokens"] > 0
    assert rows[0]["out_tokens"] == 0


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


def test_schema_validate_tolerates_raw_newlines_in_string(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    gateway = ModelGateway(_cfg(tmp_path))
    schema = {"type": "object", "required": ["markdown"], "properties": {"markdown": {"type": "string"}}}
    # Doubao-style: real \n bytes inside the JSON string instead of \\n escape.
    raw_newline_json = '{"markdown": "📍 Asia\n\n# Title\n\nbody"}'

    parsed = gateway._schema_validate(schema, raw_newline_json)

    assert parsed["markdown"].startswith("📍 Asia")
    assert "\n" in parsed["markdown"]
