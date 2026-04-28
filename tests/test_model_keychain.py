from __future__ import annotations

import subprocess

import pytest

from keypulse.pipeline import model_keychain


def test_store_secret_calls_security(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(model_keychain.subprocess, "run", fake_run)
    monkeypatch.setattr(model_keychain, "_account_name", lambda: "tester")

    model_keychain.store_secret("com.keypulse.model.cloud", "sk-test")

    assert calls == [
        [
            "security",
            "add-generic-password",
            "-s",
            "com.keypulse.model.cloud",
            "-a",
            "tester",
            "-w",
            "sk-test",
            "-U",
        ]
    ]


def test_read_secret_success(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")

    def fake_run(cmd, check=False, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 0, "  sk-live  \n", "")

    monkeypatch.setattr(model_keychain.subprocess, "run", fake_run)

    assert model_keychain.read_secret("com.keypulse.model.cloud") == "sk-live"


def test_read_secret_missing_returns_none(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")

    def fake_run(cmd, check=False, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 44, "", "could not be found")

    monkeypatch.setattr(model_keychain.subprocess, "run", fake_run)

    assert model_keychain.read_secret("com.keypulse.model.cloud") is None


def test_delete_secret_calls_security(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(model_keychain.subprocess, "run", fake_run)

    model_keychain.delete_secret("com.keypulse.model.cloud")

    assert calls == [["security", "delete-generic-password", "-s", "com.keypulse.model.cloud"]]


def test_delete_secret_ignores_missing(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")

    def fake_run(cmd, check=False, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 44, "", "could not be found")

    monkeypatch.setattr(model_keychain.subprocess, "run", fake_run)

    model_keychain.delete_secret("com.keypulse.model.cloud")


def test_keychain_unavailable_on_non_macos(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Linux")

    with pytest.raises(model_keychain.KeychainUnavailable):
        model_keychain.store_secret("service", "secret")

    with pytest.raises(model_keychain.KeychainUnavailable):
        model_keychain.delete_secret("service")


def test_read_secret_raises_on_non_macos(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Linux")

    with pytest.raises(model_keychain.KeychainUnavailable):
        model_keychain.read_secret("service")



def test_check_daemon_keychain_access(monkeypatch):
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(model_keychain, "read_secret", lambda service: None)

    assert model_keychain.check_daemon_keychain_access() is True


def test_check_daemon_keychain_access_false_when_session_create(tmp_path, monkeypatch):
    plist = tmp_path / "com.keypulse.daemon.plist"
    plist.write_bytes(
        b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict><key>SessionCreate</key><true/></dict></plist>
"""
    )
    monkeypatch.setattr(model_keychain.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(model_keychain, "_daemon_plist_path", lambda: plist)
    monkeypatch.setattr(model_keychain, "read_secret", lambda service: None)

    assert model_keychain.check_daemon_keychain_access() is False



def test_render_plist_advice_when_env_key_present(tmp_path, monkeypatch):
    plist = tmp_path / "com.keypulse.daemon.plist"
    plist.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"> 
<plist version=\"1.0\"> 
<dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ARK_API_KEY</key>
    <string>legacy</string>
  </dict>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_keychain, "_daemon_plist_path", lambda: plist)

    advice = model_keychain.render_plist_advice()

    assert "ARK_API_KEY" in advice
    assert "Keychain" in advice



def test_render_plist_advice_without_env_key(tmp_path, monkeypatch):
    plist = tmp_path / "com.keypulse.daemon.plist"
    plist.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"> 
<plist version=\"1.0\"> 
<dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_keychain, "_daemon_plist_path", lambda: plist)

    advice = model_keychain.render_plist_advice()

    assert "ARK_API_KEY" not in advice
