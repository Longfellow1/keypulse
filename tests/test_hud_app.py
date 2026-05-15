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


def test_restart_daemon_starts_self_heal_thread(hud_app, monkeypatch, tmp_path):
    runs = []
    app = hud_app.KeyPulseHUDApp()
    app.popover = _FakePopover()

    class _FakeThread:
        def __init__(self, target=None, **kwargs):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(hud_app.threading, "Thread", _FakeThread)
    monkeypatch.setitem(sys.modules, "keypulse.health.self_heal", types.SimpleNamespace(run_self_heal=lambda **kwargs: runs.append(kwargs)))

    app.restartDaemon_(None)

    assert _FakeAlert.messages[0] == "启动一键自愈？"
    assert app.popover.close_count == 1
    assert runs and runs[0]["source"] == "hud"
    assert runs[0]["dry_run"] is False
