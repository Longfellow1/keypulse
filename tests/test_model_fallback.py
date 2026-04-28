from __future__ import annotations

from urllib.error import HTTPError

import pytest

from keypulse.config import Config
from keypulse.pipeline.model import ModelGateway


def _cfg(tmp_path, profile: str = "cloud-first") -> Config:
    return Config.model_validate(
        {
            "model": {
                "active_profile": profile,
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "qwen3-8b-mlx",
                },
                "cloud": {
                    "kind": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "model": "doubao",
                    "api_key_env": "TEST_API_KEY",
                },
            }
        }
    )


def _http_error(code: int) -> HTTPError:
    return HTTPError(
        url="https://api.example.com/v1/chat/completions",
        code=code,
        msg="boom",
        hdrs=None,
        fp=None,
    )


def test_render_fallback_cloud_401_to_local(tmp_path, monkeypatch):
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-first"))
    calls: list[str] = []

    def fake_call_backend(backend, prompt: str, prompt_patch=None):
        calls.append(backend.kind)
        if backend.kind == "openai_compatible":
            raise _http_error(401)
        return "local ok"

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    assert gateway.render("hello") == "local ok"
    assert calls == ["openai_compatible", "lm_studio"]


def test_render_fallback_cloud_timeout_to_local(tmp_path, monkeypatch):
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-first"))
    calls: list[str] = []

    def fake_call_backend(backend, prompt: str, prompt_patch=None):
        calls.append(backend.kind)
        if backend.kind == "openai_compatible":
            raise TimeoutError("timeout")
        return "local ok"

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    assert gateway.render("hello") == "local ok"
    assert calls == ["openai_compatible", "lm_studio"]


def test_render_raises_last_error_when_cloud_and_local_fail(tmp_path, monkeypatch):
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-first"))
    calls: list[str] = []

    def fake_call_backend(backend, prompt: str, prompt_patch=None):
        calls.append(backend.kind)
        if backend.kind == "openai_compatible":
            raise TimeoutError("cloud timeout")
        raise RuntimeError("local failed")

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    with pytest.raises(RuntimeError, match="local failed"):
        gateway.render("hello")
    assert calls == ["openai_compatible", "lm_studio"]


def test_render_cloud_only_raises_without_local_fallback(tmp_path, monkeypatch):
    gateway = ModelGateway(_cfg(tmp_path, profile="cloud-only"))
    calls: list[str] = []

    def fake_call_backend(backend, prompt: str, prompt_patch=None):
        calls.append(backend.kind)
        raise _http_error(401)

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    with pytest.raises(HTTPError):
        gateway.render("hello")
    assert calls == ["openai_compatible"]


def test_render_local_only_raises_without_cloud_fallback(tmp_path, monkeypatch):
    gateway = ModelGateway(_cfg(tmp_path, profile="local-only"))
    calls: list[str] = []

    def fake_call_backend(backend, prompt: str, prompt_patch=None):
        calls.append(backend.kind)
        raise RuntimeError("local failed")

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    with pytest.raises(RuntimeError, match="local failed"):
        gateway.render("hello")
    assert calls == ["lm_studio"]
