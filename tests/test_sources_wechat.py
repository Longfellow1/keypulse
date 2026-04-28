from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from keypulse.sources import discover as discover_module
from keypulse.sources.discover import sources_group
from keypulse.sources.plugins.wechat import WechatSource
from keypulse.sources.registry import get_source
from keypulse.sources.wechat_probe import (
    WechatProbeResult,
    is_authorized,
    mark_authorized,
    probe,
    revoke_authorization,
)


def _completed(command: list[str], *, code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def test_probe_collects_chatlog_wechat_and_authorization(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    mark_authorized()

    app_path = tmp_path / "Applications" / "WeChat.app"
    app_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.mkdir()
    monkeypatch.setattr("keypulse.sources.wechat_probe._WECHAT_APP_CANDIDATES", (app_path,))

    def fake_run(command, **kwargs):
        if command == ["which", "chatlog"]:
            return _completed(command, code=0, stdout="/opt/homebrew/bin/chatlog\n")
        if command == ["chatlog", "--version"]:
            return _completed(command, code=0, stdout="chatlog v0.1.2\n")
        if command == ["pgrep", "-i", "wechat"]:
            return _completed(command, code=0, stdout="1234\n")
        raise AssertionError(f"unexpected command: {command!r}")

    monkeypatch.setattr("keypulse.sources.wechat_probe.subprocess.run", fake_run)

    result = probe()

    assert result.chatlog_installed is True
    assert result.chatlog_path == "/opt/homebrew/bin/chatlog"
    assert result.chatlog_version == "chatlog v0.1.2"
    assert result.wechat_running is True
    assert result.wechat_app_path == str(app_path)
    assert result.user_authorized is True


def test_probe_is_graceful_when_subprocess_fails(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr("keypulse.sources.wechat_probe._WECHAT_APP_CANDIDATES", (tmp_path / "missing.app",))

    def boom(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr("keypulse.sources.wechat_probe.subprocess.run", boom)

    result = probe()

    assert result.chatlog_installed is False
    assert result.chatlog_path is None
    assert result.chatlog_version is None
    assert result.wechat_running is False
    assert result.wechat_app_path is None
    assert result.user_authorized is False


def test_authorization_marker_lifecycle(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    assert is_authorized() is False

    mark_authorized()
    assert is_authorized() is True
    marker = home / ".keypulse" / "wechat-authorized"
    content = marker.read_text(encoding="utf-8").strip()
    assert content.startswith("authorized_at=")

    revoke_authorization()
    assert is_authorized() is False
    revoke_authorization()
    assert is_authorized() is False


def test_wechat_source_is_registered() -> None:
    assert get_source("wechat") is not None


def test_wechat_source_discover_and_read_probe_only(monkeypatch) -> None:
    source = WechatSource()
    monkeypatch.setattr(
        "keypulse.sources.plugins.wechat.probe",
        lambda: WechatProbeResult(
            chatlog_installed=True,
            chatlog_path="/opt/homebrew/bin/chatlog",
            chatlog_version="v0.1.2",
            wechat_running=True,
            wechat_app_path="/Applications/WeChat.app",
            user_authorized=True,
        ),
    )

    instances = source.discover()
    assert len(instances) == 1
    assert instances[0].plugin == "wechat"
    assert instances[0].locator == "wechat:placeholder"
    assert "warning" in instances[0].metadata

    events = list(
        source.read(
            instances[0],
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    assert events == []


def test_wechat_source_discover_returns_empty_when_preconditions_missing(monkeypatch) -> None:
    source = WechatSource()
    monkeypatch.setattr(
        "keypulse.sources.plugins.wechat.probe",
        lambda: WechatProbeResult(
            chatlog_installed=True,
            chatlog_path="/opt/homebrew/bin/chatlog",
            chatlog_version="v0.1.2",
            wechat_running=True,
            wechat_app_path="/Applications/WeChat.app",
            user_authorized=False,
        ),
    )

    assert source.discover() == []


def test_wechat_status_command_shows_all_sections(monkeypatch) -> None:
    monkeypatch.setattr(
        discover_module,
        "probe",
        lambda: WechatProbeResult(
            chatlog_installed=True,
            chatlog_path="/opt/homebrew/bin/chatlog",
            chatlog_version="chatlog v0.1.2",
            wechat_running=True,
            wechat_app_path="/Applications/WeChat.app",
            user_authorized=True,
        ),
    )
    monkeypatch.setattr(discover_module, "_detect_wechat_pid", lambda: "1234")
    monkeypatch.setattr(discover_module, "_read_wechat_authorized_at", lambda: "2026-04-28T10:00:00+00:00")

    result = CliRunner().invoke(sources_group, ["wechat", "status"])

    assert result.exit_code == 0
    assert "chatlog 二进制：✅ /opt/homebrew/bin/chatlog (v0.1.2)" in result.output
    assert "微信进程：     ✅ 运行中（PID 1234）" in result.output
    assert "用户授权：     ✅ 已授权 2026-04-28T10:00:00+00:00" in result.output
    assert "总体状态：     ✅ 就绪（前置条件全满足）" in result.output


def test_wechat_status_command_reports_missing_items(monkeypatch) -> None:
    monkeypatch.setattr(
        discover_module,
        "probe",
        lambda: WechatProbeResult(
            chatlog_installed=False,
            chatlog_path=None,
            chatlog_version=None,
            wechat_running=False,
            wechat_app_path=None,
            user_authorized=False,
        ),
    )

    result = CliRunner().invoke(sources_group, ["wechat", "status"])

    assert result.exit_code == 0
    assert "chatlog 二进制：❌ 未找到（建议：brew install chatlog）" in result.output
    assert "微信进程：     ❌ 未运行" in result.output
    assert "用户授权：     ❌ 未授权" in result.output
    assert "总体状态：     ⚠️  需补：[chatlog] [微信] [授权]" in result.output


def test_wechat_authorize_requires_explicit_yes(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    cancel = CliRunner().invoke(sources_group, ["wechat", "authorize"], input="no\n")
    assert cancel.exit_code == 0
    assert "已取消" in cancel.output
    assert is_authorized() is False

    accepted = CliRunner().invoke(sources_group, ["wechat", "authorize"], input="yes\n")
    assert accepted.exit_code == 0
    assert "你即将授权 KeyPulse 接入你的微信本地消息" in accepted.output
    assert "输入 \"yes\" 继续，其它取消：" in accepted.output
    assert "✅ 微信红区已授权" in accepted.output
    assert is_authorized() is True


def test_wechat_revoke_command(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    mark_authorized()

    revoked = CliRunner().invoke(sources_group, ["wechat", "revoke"])
    assert revoked.exit_code == 0
    assert "已撤销微信红区授权" in revoked.output
    assert is_authorized() is False

    repeated = CliRunner().invoke(sources_group, ["wechat", "revoke"])
    assert repeated.exit_code == 0
    assert "当前没有微信授权标记" in repeated.output
