from __future__ import annotations

import importlib
import sys
import types

import pytest


class _FakeAlert:
    messages: list[str] = []

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setMessageText_(self, value):
        self.message_text = value
        self.messages.append(value)

    def setInformativeText_(self, value):
        self.informative_text = value

    def addButtonWithTitle_(self, value):
        pass

    def runModal(self):
        return 1


class _FakePopover:
    def __init__(self):
        self.close_count = 0

    def performClose_(self, value):
        self.close_count += 1


class _FakeNSApp:
    terminated: list[object] = []

    @classmethod
    def terminate_(cls, value):
        cls.terminated.append(value)


@pytest.fixture()
def hud_app(monkeypatch):
    appkit = types.SimpleNamespace(
        NSObject=object,
        NSAlert=_FakeAlert,
        NSAlertFirstButtonReturn=1,
        NSApp=_FakeNSApp,
        NSStatusBar=types.SimpleNamespace(systemStatusBar=lambda: None),
        NSVariableStatusItemLength=0,
        NSPopover=types.SimpleNamespace(alloc=lambda: None),
        NSPopoverBehaviorTransient=0,
        NSViewController=types.SimpleNamespace(alloc=lambda: None),
        NSTextField=types.SimpleNamespace(alloc=lambda: None),
        NSImage=types.SimpleNamespace(),
        NSImageOnly=0,
        NSApplication=types.SimpleNamespace(sharedApplication=lambda: None),
        NSApplicationActivationPolicyAccessory=0,
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: None),
        NSViewWidthSizable=1,
        NSViewHeightSizable=2,
        NSMakeRect=lambda *args: args,
        NSMakeSize=lambda *args: args,
    )
    objc = types.SimpleNamespace(
        IBAction=lambda func: func,
        selector=lambda func, signature=None: func,
        super=super,
    )
    foundation = types.SimpleNamespace(NSTimer=object, NSURL=object)
    webkit = types.SimpleNamespace(
        WKNavigationActionPolicyAllow=1,
        WKNavigationActionPolicyCancel=0,
        WKWebView=object,
        WKWebViewConfiguration=object,
    )

    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "objc", objc)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "WebKit", webkit)
    sys.modules.pop("keypulse.hud.app", None)

    _FakeAlert.messages = []
    _FakeNSApp.terminated = []
    module = importlib.import_module("keypulse.hud.app")
    yield module
    sys.modules.pop("keypulse.hud.app", None)


def test_toggle_pause_keeps_popover_open(hud_app, monkeypatch):
    writes = []
    app = hud_app.KeyPulseHUDApp()
    app.capture_status = "running"
    app.popover = _FakePopover()
    app.refresh = lambda: writes.append(("refresh", None))

    monkeypatch.setattr(hud_app, "set_state", lambda key, value: writes.append((key, value)))

    app.togglePause_(None)

    assert ("status", "paused") in writes
    assert ("refresh", None) in writes
    assert app.popover.close_count == 0


def test_restart_daemon_without_hud_launchd_restarts_hud_cli_and_terminates(hud_app, monkeypatch, tmp_path):
    popen_calls = []
    app = hud_app.KeyPulseHUDApp()
    app.popover = _FakePopover()

    def fake_popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(hud_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hud_app.os, "getuid", lambda: 501)
    monkeypatch.setattr(hud_app.sys, "argv", ["/usr/local/bin/keypulse"])
    monkeypatch.setattr(hud_app, "_hud_launchd_plist_path", lambda: tmp_path / "com.keypulse.hud.plist")

    app.restartDaemon_(None)

    assert _FakeAlert.messages[0] == "重启 KeyPulse（daemon + HUD）？"
    assert app.popover.close_count == 1
    assert popen_calls[0][0] == ["launchctl", "kickstart", "-k", "gui/501/com.keypulse.daemon"]
    assert popen_calls[1][0] == ["/usr/local/bin/keypulse", "hud"]
    assert popen_calls[1][1]["start_new_session"] is True
    assert popen_calls[1][1]["stdin"] is hud_app.subprocess.DEVNULL
    assert popen_calls[1][1]["stdout"] is hud_app.subprocess.DEVNULL
    assert popen_calls[1][1]["stderr"] is hud_app.subprocess.DEVNULL
    assert _FakeNSApp.terminated == [None]


def test_restart_daemon_with_hud_launchd_kickstarts_hud_job(hud_app, monkeypatch, tmp_path):
    popen_calls = []
    app = hud_app.KeyPulseHUDApp()
    app.popover = _FakePopover()
    hud_plist = tmp_path / "com.keypulse.hud.plist"
    hud_plist.write_text("<plist/>")

    def fake_popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(hud_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hud_app.os, "getuid", lambda: 501)
    monkeypatch.setattr(hud_app, "_hud_launchd_plist_path", lambda: hud_plist)

    app.restartDaemon_(None)

    assert popen_calls[0][0] == ["launchctl", "kickstart", "-k", "gui/501/com.keypulse.daemon"]
    assert popen_calls[1][0] == ["launchctl", "kickstart", "-k", "gui/501/com.keypulse.hud"]
    assert "start_new_session" not in popen_calls[1][1]
    assert _FakeNSApp.terminated == []
