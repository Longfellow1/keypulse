from __future__ import annotations

import tomllib

import pytest
from click.testing import CliRunner

from penpal.cli import main
from penpal.config import Config, normalize_url_deny_host, set_url_deny_host


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".penpal" / "config.toml"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://WWW.Example.COM:8443/path?q=1", "example.com"),
        ("www.EXAMPLE.com/path", "example.com"),
        ("example.com:8080", "example.com"),
        ("Example.COM", "example.com"),
    ],
)
def test_normalize_url_deny_host(value, expected):
    assert normalize_url_deny_host(value) == expected
    assert normalize_url_deny_host(expected) == expected


def test_set_url_deny_host_roundtrip_preserves_other_config(monkeypatch, tmp_path):
    config_path = _home(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '# retain this comment\n'
        '[app]\nflush_interval_sec = 9\n\n'
        '[privacy]\nurl_deny_hosts = ["custom.example"]\nredact_emails = false\n\n'
        '[watchers]\nocr = true\n',
        encoding="utf-8",
    )

    host, blocked = set_url_deny_host("https://WWW.New.Example:443/a", True)
    assert host == "new.example"
    assert blocked == ["custom.example", "new.example"]
    assert set_url_deny_host("new.example", True)[1] == blocked
    assert Config.load().privacy.url_deny_hosts == blocked

    unblocked_host, remaining = set_url_deny_host("WWW.NEW.EXAMPLE:9000/path", False)
    assert unblocked_host == "new.example"
    assert remaining == ["custom.example"]
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["privacy"]["url_deny_hosts"] == ["custom.example"]
    assert parsed["privacy"]["redact_emails"] is False
    assert parsed["app"]["flush_interval_sec"] == 9
    assert parsed["watchers"]["ocr"] is True
    assert "# retain this comment" in config_path.read_text(encoding="utf-8")


def test_privacy_site_commands_roundtrip_in_tmp_home(monkeypatch, tmp_path):
    config_path = _home(monkeypatch, tmp_path)
    runner = CliRunner()

    blocked = runner.invoke(main, ["privacy", "block-site", "HTTPS://WWW.Example.com:8443/path"])
    assert blocked.exit_code == 0, blocked.output
    assert "example.com" in blocked.output
    configured = tomllib.loads(config_path.read_text(encoding="utf-8"))["privacy"]["url_deny_hosts"]
    assert configured.count("example.com") == 1

    repeated = runner.invoke(main, ["privacy", "block-site", "example.com"])
    assert repeated.exit_code == 0, repeated.output
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["privacy"]["url_deny_hosts"].count("example.com") == 1

    listed = runner.invoke(main, ["privacy", "blocked-sites"])
    assert listed.exit_code == 0, listed.output
    assert "example.com" in listed.output

    unblocked = runner.invoke(main, ["privacy", "unblock-site", "http://WWW.Example.COM:80/path"])
    assert unblocked.exit_code == 0, unblocked.output
    assert "example.com" not in tomllib.loads(config_path.read_text(encoding="utf-8"))["privacy"]["url_deny_hosts"]
