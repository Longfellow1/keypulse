from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import main


def test_model_setup_writes_config_and_stores_keychain(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"

    monkeypatch.setattr("keypulse.cli.get_config_path", lambda: config_path)
    monkeypatch.setattr("keypulse.cli.check_daemon_keychain_access", lambda: True)
    monkeypatch.setattr("keypulse.cli.render_plist_advice", lambda: "plist advice")

    stored: dict[str, str] = {}

    def fake_store_secret(service: str, key: str) -> None:
        stored[service] = key

    monkeypatch.setattr("keypulse.cli.store_secret", fake_store_secret)

    prompts = iter(
        [
            "1",  # cloud preset: doubao
            "",   # cloud model default
            "sk-test",  # cloud api key
            "1",  # local preset: lm studio
            "",   # local model default
            "1",  # policy cloud-first
        ]
    )

    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("keypulse.cli._probe_backend", lambda *args, **kwargs: (True, "OK", 1.4))

    result = CliRunner().invoke(main, ["model", "setup"])

    assert result.exit_code == 0
    assert "API Key" in result.output
    assert "Keychain" in result.output
    assert stored["com.keypulse.model.cloud"] == "sk-test"

    text = config_path.read_text(encoding="utf-8")
    assert 'active_profile = "cloud-first"' in text
    assert 'api_key_source = "keychain:com.keypulse.model.cloud"' in text
    assert "sk-test" not in text



def test_model_setup_connectivity_failure_does_not_block_save(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("keypulse.cli.get_config_path", lambda: config_path)
    monkeypatch.setattr("keypulse.cli.check_daemon_keychain_access", lambda: True)
    monkeypatch.setattr("keypulse.cli.render_plist_advice", lambda: "")
    monkeypatch.setattr("keypulse.cli.store_secret", lambda service, key: None)

    prompts = iter(["4", "https://example.com/v1", "my-model", "sk-test", "3", "1"])
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("keypulse.cli._probe_backend", lambda *args, **kwargs: (False, "failed", 0.2))

    result = CliRunner().invoke(main, ["model", "setup"])

    assert result.exit_code == 0
    assert "仍保存配置" in result.output
    assert config_path.exists()


def test_model_setup_skip_local_writes_disabled_backend(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("keypulse.cli.get_config_path", lambda: config_path)
    monkeypatch.setattr("keypulse.cli.check_daemon_keychain_access", lambda: True)
    monkeypatch.setattr("keypulse.cli.render_plist_advice", lambda: "")
    monkeypatch.setattr("keypulse.cli.store_secret", lambda service, key: None)

    prompts = iter(["1", "", "sk-test", "3", "1"])
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("keypulse.cli._probe_backend", lambda *args, **kwargs: (True, "OK", 0.1))

    result = CliRunner().invoke(main, ["model", "setup"])

    assert result.exit_code == 0
    text = config_path.read_text(encoding="utf-8")
    assert '[model.local]' in text
    assert 'kind = "disabled"' in text



def test_model_status_shows_two_backends_and_short_circuit(monkeypatch, tmp_path):
    state_path = tmp_path / "model-state.json"
    state_path.write_text(
        """
{
  "active_profile": "cloud-first",
  "short_circuits": {
    "cloud": {"until": "2999-01-01T00:00:00+00:00", "reason": "auth", "fail_count": 3},
    "local": null
  },
  "last_call": {
    "cloud": {"at": "2026-04-28T14:00:00+00:00", "duration_ms": 1400, "ok": true}
  }
}
""".strip(),
        encoding="utf-8",
    )

    cfg = type(
        "Cfg",
        (),
        {
            "model": type(
                "ModelCfg",
                (),
                {
                    "active_profile": "cloud-first",
                    "state_path": str(state_path),
                    "local": type(
                        "LocalCfg",
                        (),
                        {
                            "kind": "lm_studio",
                            "base_url": "http://127.0.0.1:1234",
                            "model": "qwen3-8b-mlx",
                            "api_key_env": "",
                            "api_key_source": "",
                            "timeout_sec": 20,
                        },
                    )(),
                    "cloud": type(
                        "CloudCfg",
                        (),
                        {
                            "kind": "openai_compatible",
                            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                            "model": "doubao-seed-1-6-250615",
                            "api_key_env": "ARK_API_KEY",
                            "api_key_source": "keychain:com.keypulse.model.cloud",
                            "timeout_sec": 300,
                        },
                    )(),
                },
            )(),
        },
    )()

    monkeypatch.setattr("keypulse.cli.get_config", lambda: cfg)

    result = CliRunner().invoke(main, ["model", "status"])

    assert result.exit_code == 0
    assert "Profile: cloud-first" in result.output
    assert "[cloud]" in result.output
    assert "[local]" in result.output
    assert "Short-circuit" in result.output


def test_model_status_plain_prints_backend_order(monkeypatch, tmp_path):
    state_path = tmp_path / "model-state.json"
    state_path.write_text("{}", encoding="utf-8")

    cfg = type(
        "Cfg",
        (),
        {
            "model": type(
                "ModelCfg",
                (),
                {
                    "active_profile": "cloud-first",
                    "state_path": str(state_path),
                    "local": type(
                        "LocalCfg",
                        (),
                        {
                            "kind": "lm_studio",
                            "base_url": "http://127.0.0.1:1234",
                            "model": "qwen3-8b-mlx",
                            "api_key_env": "",
                            "api_key_source": "",
                            "timeout_sec": 20,
                        },
                    )(),
                    "cloud": type(
                        "CloudCfg",
                        (),
                        {
                            "kind": "openai_compatible",
                            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                            "model": "doubao-seed-1-6-250615",
                            "api_key_env": "ARK_API_KEY",
                            "api_key_source": "keychain:com.keypulse.model.cloud",
                            "timeout_sec": 300,
                        },
                    )(),
                },
            )(),
        },
    )()
    monkeypatch.setattr("keypulse.cli.get_config", lambda: cfg)

    result = CliRunner().invoke(main, ["model", "status", "--plain"])

    assert result.exit_code == 0
    assert "profile=cloud-first" in result.output
    assert "order=cloud,local" in result.output
