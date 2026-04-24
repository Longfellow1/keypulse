from __future__ import annotations

import json

import pytest

from keypulse.config import Config
from keypulse.pipeline.model import ModelGateway


def _make_gateway(tmp_path, kind: str = "lm_studio") -> ModelGateway:
    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "local-first",
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": kind,
                    "base_url": "http://127.0.0.1:1234",
                    "model": "test-model",
                },
            }
        }
    )
    return ModelGateway(cfg)


def test_render_calls_underlying_client_with_bare_prompt(tmp_path, monkeypatch):
    """render() must send only a single user message with the raw prompt — no system message, no template."""
    gateway = _make_gateway(tmp_path)
    captured: dict[str, object] = {}

    def fake_request_json(backend, path, payload, method="POST"):
        captured["path"] = path
        captured["payload"] = json.loads(json.dumps(payload))  # deep copy
        return {"choices": [{"message": {"content": "bare response"}}]}

    monkeypatch.setattr(gateway, "_request_json", fake_request_json)

    result = gateway.render("Hello, world!")

    assert result == "bare response"
    assert captured["path"] == "/v1/chat/completions"
    messages = captured["payload"]["messages"]
    assert len(messages) == 1, "render() must send exactly one message (no system)"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello, world!"


def test_render_daily_narrative_unchanged(tmp_path, monkeypatch):
    """render_daily_narrative() must still prepend a system message with the template prompt."""
    gateway = _make_gateway(tmp_path)
    captured: dict[str, object] = {}

    def fake_request_json(backend, path, payload, method="POST"):
        captured["path"] = path
        captured["payload"] = json.loads(json.dumps(payload))
        return {"choices": [{"message": {"content": "narrative output"}}]}

    monkeypatch.setattr(gateway, "_request_json", fake_request_json)

    result = gateway.render_daily_narrative(work_blocks=[], prompt_patch="patch hint")

    assert result == "narrative output"
    messages = captured["payload"]["messages"]
    # _call_backend with prompt_patch puts system=patch, user=template-prompt
    assert any(m["role"] == "system" for m in messages), "render_daily_narrative must include a system message"
    system_msg = next(m for m in messages if m["role"] == "system")
    assert "patch hint" in system_msg["content"]
    # user message should contain the Chinese template rules
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "根据结构化工作块" in user_msg["content"]


def test_render_raises_when_backend_disabled(tmp_path):
    """render() must raise RuntimeError when profile is privacy-locked (no usable backend)."""
    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "privacy-locked",
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "local-model",
                },
            }
        }
    )
    gateway = ModelGateway(cfg)

    with pytest.raises(RuntimeError, match="no backend available"):
        gateway.render("test prompt")
