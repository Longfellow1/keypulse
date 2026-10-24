"""HUD AppKit shell: it only routes page messages to the controller and draws the model it gets back.

No window, status item or WebView is created: the ObjC objects are built bare and given fakes.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("AppKit")
pytest.importorskip("WebKit")

from Foundation import NSDictionary  # noqa: E402

from penpal.hud import app as hud_app  # noqa: E402

MODEL = {"title": "概览", "tabs": [{"id": "overview", "badge": "amber"}], "status": {}, "sections": []}


class _Controller:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def dispatch(self, action, args=None):
        self.calls.append((action, args))
        return {**MODEL, "title": f"after {action}"}

    def model(self, fresh=False):
        self.calls.append(("model", fresh))
        return MODEL

    def popover_closed(self):
        self.closed += 1


class _WebView:
    def __init__(self):
        self.scripts = []

    def evaluateJavaScript_completionHandler_(self, script, handler):
        self.scripts.append(script)


@pytest.fixture()
def shell():
    app = hud_app.PenpalHUDApp.alloc().init()
    app.controller, app.webview, app.status_item = _Controller(), _WebView(), None
    return app


def _rendered(shell):
    script = shell.webview.scripts[-1]
    assert script.startswith("window.render(") and script.endswith(");")
    return json.loads(script[len("window.render("):-2])


def test_page_message_goes_to_the_controller_and_the_model_is_drawn(shell):
    shell.handle_message({"action": "capture.pause", "args": {}})
    assert shell.controller.calls == [("capture.pause", {})]
    assert _rendered(shell)["title"] == "after capture.pause"


def test_ready_message_pushes_the_current_model_without_an_action(shell):
    shell.handle_message({"action": "ui.ready"})
    assert shell.controller.calls == [("model", False)] and _rendered(shell)["title"] == "概览"


def test_junk_messages_are_ignored(shell):
    shell.handle_message("nope")
    shell.handle_message(None)
    assert shell.controller.calls == [] and shell.webview.scripts == []


def test_script_is_safe_to_embed(shell):
    shell.controller.model = lambda fresh=False: {**MODEL, "title": "</script><b>"}
    shell.handle_message({"action": "ui.ready"})
    assert "</script><b>" not in shell.webview.scripts[-1]


def test_message_body_from_javascript_becomes_plain_python():
    body = NSDictionary.dictionaryWithDictionary_({"action": "source.toggle", "args": {"name": "clipboard", "enable": False}})
    plain = hud_app._plain(body)
    assert plain == {"action": "source.toggle", "args": {"name": "clipboard", "enable": False}}
    assert type(plain) is dict and type(plain["args"]) is dict and plain["args"]["enable"] is False


def test_closing_the_popover_tells_the_controller_to_forget_transient_state(shell):
    shell.popoverDidClose_(None)
    assert shell.controller.closed == 1


def test_message_handler_forwards_to_the_shell():
    seen = []
    handler = hud_app.MessageHandler.alloc().initWithShell_(types.SimpleNamespace(handle_message=seen.append))
    handler.userContentController_didReceiveScriptMessage_(None, types.SimpleNamespace(body=lambda: {"action": "nav.back"}))
    assert seen == [{"action": "nav.back"}]


def test_navigation_is_limited_to_the_loaded_page(shell):
    decisions = []

    def action(url):
        return types.SimpleNamespace(request=lambda: types.SimpleNamespace(URL=lambda: url))

    for scheme, expected in [("about", hud_app.WKNavigationActionPolicyAllow), ("https", hud_app.WKNavigationActionPolicyCancel),
                             ("file", hud_app.WKNavigationActionPolicyCancel)]:
        shell.webView_decidePolicyForNavigationAction_decisionHandler_(None, action(types.SimpleNamespace(scheme=lambda s=scheme: s)), decisions.append)
        assert decisions[-1] == expected
