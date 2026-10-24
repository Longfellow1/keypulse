"""D33 CLI parity: chat-app opt-in, `health` alias, launchd plists use the CLI executable."""
from __future__ import annotations

import tomllib
from pathlib import Path

from click.testing import CliRunner

from penpal.cli import main
from penpal.cli.install import _launchd_jobs
from penpal.config import Config, write_chat_apps_opt_in

ROOT = Path(__file__).resolve().parent.parent


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".penpal" / "config.toml"


def _opt_in(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))["privacy"]["chat_apps_opt_in"]


def test_chat_apps_list_shows_all_denied_by_default(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    result = CliRunner().invoke(main, ["privacy", "chat-apps", "list", "--plain"])
    assert result.exit_code == 0
    lines = dict(line.split("\t") for line in result.output.strip().splitlines())
    assert lines["WeChat"] == "denied" and "Slack" in lines and set(lines.values()) == {"denied"}


def test_allow_then_deny_roundtrip_and_list(monkeypatch, tmp_path):
    cfg = _home(monkeypatch, tmp_path)
    r = CliRunner().invoke(main, ["privacy", "chat-apps", "allow", "wechat"])
    assert r.exit_code == 0 and "Private" in r.output
    assert _opt_in(cfg) == ["WeChat"]
    assert Config.load().privacy.chat_apps_opt_in == ["WeChat"]
    listed = CliRunner().invoke(main, ["privacy", "chat-apps", "list", "--plain"]).output
    assert "WeChat\tallowed" in listed and "Slack\tdenied" in listed
    CliRunner().invoke(main, ["privacy", "chat-apps", "allow", "Slack"])
    assert _opt_in(cfg) == ["WeChat", "Slack"]
    r = CliRunner().invoke(main, ["privacy", "chat-apps", "deny", "WeChat"])
    assert r.exit_code == 0 and "Private" not in r.output
    assert _opt_in(cfg) == ["Slack"]


def test_allow_is_idempotent(monkeypatch, tmp_path):
    cfg = _home(monkeypatch, tmp_path)
    for _ in range(2):
        assert CliRunner().invoke(main, ["privacy", "chat-apps", "allow", "QQ"]).exit_code == 0
    assert _opt_in(cfg) == ["QQ"]


def test_unknown_name_lists_valid_names_and_writes_nothing(monkeypatch, tmp_path):
    cfg = _home(monkeypatch, tmp_path)
    r = CliRunner().invoke(main, ["privacy", "chat-apps", "allow", "NoSuchApp"])
    assert r.exit_code != 0
    assert "NoSuchApp" in r.output and "WeChat" in r.output and "Slack" in r.output
    assert not cfg.exists()


def test_writer_preserves_other_content(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '# my comment\n[app]\nflush_interval_sec = 9\n\n[privacy]\n# keep\nchat_apps_opt_in = [\n  "QQ",\n]\nblacklist_patterns = ["x"]\n\n[watchers]\nocr = true\n',
        encoding="utf-8",
    )
    write_chat_apps_opt_in(p, ["QQ", "Slack"])
    text = p.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["privacy"]["chat_apps_opt_in"] == ["QQ", "Slack"]
    assert data["privacy"]["blacklist_patterns"] == ["x"]
    assert data["app"]["flush_interval_sec"] == 9 and data["watchers"]["ocr"] is True
    assert "# my comment" in text and "# keep" in text


def test_writer_adds_key_to_existing_privacy_table_and_new_table(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[privacy]\nblacklist_patterns = ["x"]\n[watchers]\nocr = true\n', encoding="utf-8")
    write_chat_apps_opt_in(p, ["Signal"])
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["privacy"]["chat_apps_opt_in"] == ["Signal"] and data["watchers"]["ocr"] is True
    q = tmp_path / "b.toml"
    q.write_text("[app]\nflush_interval_sec = 3\n", encoding="utf-8")
    write_chat_apps_opt_in(q, ["LINE"])
    assert tomllib.loads(q.read_text(encoding="utf-8"))["privacy"]["chat_apps_opt_in"] == ["LINE"]


def test_health_is_alias_of_healthcheck(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    calls = []

    def fake(config_path=None):
        calls.append(config_path)
        return {"overall": "ok", "alerts": []}

    monkeypatch.setattr("penpal.health.run_healthcheck", fake)
    for name in ("health", "healthcheck"):
        r = CliRunner().invoke(main, [name])
        assert r.exit_code == 0 and '"overall": "ok"' in r.output
    assert len(calls) == 2
    assert "health" in CliRunner().invoke(main, ["--help"]).output


def test_launchd_plists_use_cli_executable():
    exe = Path("/opt/x/bin/penpal")
    for payload in _launchd_jobs(exe).values():
        assert payload["ProgramArguments"][0] == str(exe)
        assert "Penpal.app" not in str(payload)


def test_no_static_plist_or_app_path_in_install_paths():
    assert not (ROOT / "integrations" / "launchd").exists()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("install-plists:")[1].split("\n\n")[0]
    assert "install launchd" in block and "Penpal.app" not in block
    assert "Penpal.app/Contents/MacOS" not in (ROOT / "install.sh").read_text(encoding="utf-8")
