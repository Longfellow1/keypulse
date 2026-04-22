from __future__ import annotations

import json

from keypulse.config import Config
from keypulse.pipeline.model import ModelGateway


def test_model_gateway_prefers_state_profile_over_config_default(tmp_path):
    state_path = tmp_path / "model-state.json"
    state_path.write_text(json.dumps({"active_profile": "cloud-first"}))

    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "local-first",
                "state_path": str(state_path),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "local-model",
                },
                "cloud": {
                    "kind": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "model": "cloud-model",
                    "api_key_env": "KEYPULSE_TEST_API_KEY",
                },
            }
        }
    )

    gateway = ModelGateway(cfg)

    assert gateway.active_profile == "cloud-first"
    assert gateway.select_backend("write").model == "cloud-model"


def test_model_gateway_privacy_locked_disables_backend(tmp_path):
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

    assert gateway.select_backend("mine").kind == "disabled"


def test_normalize_markdown_keeps_prompt_patch_out_of_user_prompt(tmp_path, monkeypatch):
    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "local-first",
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
    seen: dict[str, object] = {}

    def fake_request_json(backend, path, payload, method="POST"):
        seen["path"] = path
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "normalized output"}}]}

    monkeypatch.setattr(gateway, "_request_json", fake_request_json)

    result = gateway.normalize_markdown("## Title\nbody", prompt_patch="Keep Chinese terms unchanged.")

    assert result == "normalized output"
    assert seen["path"] == "/v1/chat/completions"
    payload = seen["payload"]
    assert payload["messages"][0]["role"] == "system"
    assert "Keep Chinese terms unchanged." in payload["messages"][0]["content"]
    assert "Patch:" not in payload["messages"][1]["content"]
    assert "Keep Chinese terms unchanged." not in payload["messages"][1]["content"]
