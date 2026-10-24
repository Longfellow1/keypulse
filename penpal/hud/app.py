"""Thin AppKit shell for the HUD: a menu bar icon, a 360x600 popover hosting the WebView, and message routing.

All state and actions live in ``HudController`` (pure Python, same core functions as the CLI); all drawing
lives in ``render.py``. This file only connects them to AppKit, so it holds no business logic.
The daemon keeps every macOS permission; this process needs none.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time

import AppKit
import objc
from Foundation import NSObject, NSTimer
from WebKit import (
    WKNavigationActionPolicyAllow,
    WKNavigationActionPolicyCancel,
    WKUserContentController,
    WKWebView,
    WKWebViewConfiguration,
)

from penpal.capabilities.store import load_states as load_capability_states
from penpal.config import Config
from penpal.hud.approval_window import ApprovalWindowController, safe_pending_candidate_count
from penpal.hud.controller import HudController
from penpal.hud.render import HUD_HEIGHT, HUD_WIDTH, build_html
from penpal.hud.reload import HUDReloader
from penpal.runtime.evaluate import current_identity, loaded_identity
from penpal.store.repository import get_state, set_state

REFRESH_SEC = 15.0
MESSAGE_HANDLER_NAME = "penpal"
BADGE_COLORS = {"red": (0.77, 0.28, 0.23), "amber": (0.78, 0.54, 0.16)}


def _copy_to_pasteboard(text: str) -> None:
    board = AppKit.NSPasteboard.generalPasteboard()
    board.clearContents()
    board.setString_forType_(text, AppKit.NSPasteboardTypeString)


def _plain(value):
    """NSDictionary / NSArray / NSString coming from JS -> plain Python containers."""
    if isinstance(value, dict) or hasattr(value, "keys"):
        return {str(k): _plain(value[k]) for k in value.keys()}
    if isinstance(value, (list, tuple)) or hasattr(value, "objectAtIndex_"):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)


class MessageHandler(NSObject):
    """WKScriptMessageHandler: forwards every tap from the page to the shell."""

    def initWithShell_(self, shell):
        self = objc.super(MessageHandler, self).init()
        if self is None:
            return None
        self.shell = shell
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        self.shell.handle_message(_plain(message.body()))


class PenpalHUDApp(NSObject):
    def initWithConfig_(self, cfg: Config):
        self = objc.super(PenpalHUDApp, self).init()
        if self is None:
            return None
        self.cfg = cfg
        self._hud_reloader = HUDReloader(loaded_identity(cfg), lambda: current_identity(cfg))
        self._reload_scheduled = False
        self.controller = HudController(Config.load, copy_to_clipboard=_copy_to_pasteboard)
        self.pending_count = 0
        self.pending_count_error = None
        self.approval_window = None
        self._pending_count_last_sync = 0.0
        self._message_handler = MessageHandler.alloc().initWithShell_(self)

        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._install_status_icon()
        button = self.status_item.button()
        button.setTarget_(self)
        button.setAction_(objc.selector(self.togglePopover_, signature=b"v@:@"))
        if hasattr(button, "sendActionOn_"):
            events = int(getattr(AppKit, "NSEventMaskLeftMouseUp", 0)) | int(getattr(AppKit, "NSEventMaskRightMouseUp", 0))
            if events:
                button.sendActionOn_(events)
        self._setup_status_menu()
        self._maybe_prompt_browser_automation_guide()

        self.popover = AppKit.NSPopover.alloc().init()
        self.popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        self.popover.setDelegate_(self)
        self.popover_vc = AppKit.NSViewController.alloc().init()
        self.popover.setContentViewController_(self.popover_vc)
        self.popover.setContentSize_(AppKit.NSMakeSize(HUD_WIDTH, HUD_HEIGHT))
        self.popover_vc.setView_(self._build_webview())
        self._push_model(fresh=True)
        return self

    # ── WebView ─────────────────────────────────────────────────────────────

    @objc.python_method
    def _build_webview(self):
        config = WKWebViewConfiguration.alloc().init()
        content = WKUserContentController.alloc().init()
        content.addScriptMessageHandler_name_(self._message_handler, MESSAGE_HANDLER_NAME)
        config.setUserContentController_(content)
        frame = AppKit.NSMakeRect(0.0, 0.0, HUD_WIDTH, HUD_HEIGHT)
        webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        webview.setNavigationDelegate_(self)
        if hasattr(webview, "setValue_forKey_"):
            webview.setValue_forKey_(False, "drawsBackground")
        webview.loadHTMLString_baseURL_(build_html(), None)
        self.webview = webview
        return webview

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, _webview, action, decision_handler):
        # The page is loaded once from a string; it never needs to navigate anywhere.
        url = action.request().URL()
        scheme = str(url.scheme() or "").lower() if url is not None else ""
        decision_handler(WKNavigationActionPolicyAllow if scheme in ("", "about") else WKNavigationActionPolicyCancel)

    @objc.python_method
    def handle_message(self, body) -> None:
        """One tap from the page: route it to the controller and draw the model it returns."""
        if not isinstance(body, dict):
            return
        action = body.get("action")
        if action == "ui.ready":
            self._push_model()
            return
        self._push(self.controller.dispatch(action, body.get("args")))

    @objc.python_method
    def _push_model(self, fresh: bool = False) -> None:
        self._push(self.controller.model(fresh=fresh))

    @objc.python_method
    def _push(self, model: dict) -> None:
        self._set_badge(model["tabs"][0].get("badge"))
        webview = getattr(self, "webview", None)
        if webview is None:
            return
        payload = json.dumps(model, ensure_ascii=False).replace('</', '<\\/')
        script = f"window.render({payload});"
        try:
            webview.evaluateJavaScript_completionHandler_(script, None)
        except Exception:
            pass  # the first full page load renders the initial model via ui.ready

    # ── status item ─────────────────────────────────────────────────────────

    @objc.python_method
    def _install_status_icon(self) -> None:
        button = self.status_item.button()
        button.setTitle_("")
        image = None
        if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("square.and.pencil", "Penpal")
        if image is None:
            image = AppKit.NSImage.imageNamed_("NSActionTemplate")
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setImagePosition_(AppKit.NSImageLeft)

    @objc.python_method
    def _set_badge(self, tone) -> None:
        """A small coloured dot next to the icon when something needs attention (same tone as the Overview tab)."""
        button = self.status_item.button() if getattr(self, "status_item", None) is not None else None
        if button is None:
            return
        try:
            if tone in BADGE_COLORS:
                r, g, b = BADGE_COLORS[tone]
                color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
                attrs = {AppKit.NSForegroundColorAttributeName: color}
                button.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(" ●", attrs))
            else:
                button.setTitle_("")
        except Exception:
            pass

    @objc.python_method
    def _maybe_prompt_browser_automation_guide(self) -> None:
        if str(get_state("browser_automation_guide_shown") or "").strip() == "1":
            return
        state = load_capability_states().get("browser_automation")
        if state is None or state.ok or state.code != "browser_automation_denied":
            return
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("请开启浏览器自动化权限")
        alert.setInformativeText_(
            "请前往系统设置 → 隐私与安全性 → 自动化，勾选 Penpal 对 Safari / Google Chrome / Arc / Microsoft Edge / Brave Browser 的控制权限。"
        )
        alert.addButtonWithTitle_("知道了")
        alert.runModal()
        set_state("browser_automation_guide_shown", "1")

    @objc.IBAction
    def togglePopover_(self, sender):
        if self._is_secondary_click():
            self._refresh_approval_menu_item()
            if getattr(self, "status_menu", None) is not None and hasattr(self.status_item, "popUpStatusItemMenu_"):
                self.status_item.popUpStatusItemMenu_(self.status_menu)
            return
        if self.popover.isShown():
            self.popover.performClose_(None)
            return
        self._push_model(fresh=True)
        self.popover.showRelativeToRect_ofView_preferredEdge_(sender.bounds(), sender, 1)

    def popoverDidClose_(self, _notification):
        self.controller.popover_closed()  # forgets a just-shown secret
        self._push_model()

    def pollSignals_(self, _timer):
        if not self._reload_scheduled:
            try:
                if self._hud_reloader.check():
                    self._reload_scheduled = True
                    self.performSelector_withObject_afterDelay_(
                        objc.selector(self.reloadAfterTimer_, signature=b"v@:@"), None, 0.0
                    )
                    return
            except (OSError, RuntimeError, ValueError) as exc:
                logging.getLogger(__name__).warning(
                    "HUD runtime identity check failed: %s", type(exc).__name__
                )
        self._sync_pending_count()
        if self.popover.isShown():
            self._push_model(fresh=True)
        else:
            self._set_badge(self.controller.model(fresh=True)["tabs"][0].get("badge"))

    def reloadAfterTimer_(self, _sender):
        """Replace the process on the next run-loop turn, after the timer callback has returned."""
        self._hud_reloader.exec()

    # ── right-click menu (candidate approval, daemon restart, quit) ─────────

    @objc.python_method
    def _setup_status_menu(self) -> None:
        self.status_menu = AppKit.NSMenu.alloc().init()
        self.approval_menu_item = self._menu_item("候选审批", self.openApprovalWindow_)
        self.status_menu.addItem_(self.approval_menu_item)
        self.status_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.status_menu.addItem_(self._menu_item("重启后台服务", self.restartDaemon_))
        self.status_menu.addItem_(self._menu_item("退出 HUD", self.confirmQuit_))

    @objc.python_method
    def _menu_item(self, title, handler):
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, objc.selector(handler, signature=b"v@:@"), "")
        item.setTarget_(self)
        return item

    @objc.python_method
    def _sync_pending_count(self) -> None:
        now = time.monotonic()
        if now - self._pending_count_last_sync < 60.0:
            return
        count, error = safe_pending_candidate_count()
        self.pending_count_error = error
        if count is not None:
            self.pending_count = int(count)
        self._pending_count_last_sync = now
        self._refresh_approval_menu_item()

    @objc.python_method
    def _refresh_approval_menu_item(self) -> None:
        item = getattr(self, "approval_menu_item", None)
        if item is None:
            return
        if self.pending_count_error:
            item.setTitle_("候选审批 (?)")
            item.setEnabled_(True)
            return
        count = max(int(self.pending_count), 0)
        item.setTitle_(f"候选审批 ({count})" if count else "候选审批")
        item.setEnabled_(count > 0)

    @objc.python_method
    def _is_secondary_click(self) -> bool:
        if not hasattr(AppKit.NSApp, "currentEvent"):
            return False
        event = AppKit.NSApp.currentEvent()
        if event is None or not hasattr(event, "type"):
            return False
        return int(event.type()) in {int(getattr(AppKit, "NSEventTypeRightMouseUp", -1)), int(getattr(AppKit, "NSEventTypeRightMouseDown", -2))}

    @objc.IBAction
    def openApprovalWindow_(self, _sender):
        if self.pending_count_error:
            self._pending_count_last_sync = 0.0
            self._sync_pending_count()
            if self.pending_count_error:
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("候选审批读取失败")
                alert.setInformativeText_(self.pending_count_error)
                alert.addButtonWithTitle_("OK")
                alert.runModal()
                return
        if self.approval_window is None:
            self.approval_window = ApprovalWindowController.alloc().initWithStore_(None)
        self.approval_window.showWindow_(None)
        self._pending_count_last_sync = 0.0

    @objc.IBAction
    def restartDaemon_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("重启后台服务？")
        alert.setInformativeText_("Penpal 会通过 launchd 重启后台服务并确认新代次开始推进。")
        alert.addButtonWithTitle_("重启")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            return

        def _run_restart() -> None:
            from penpal.runtime.control import restart

            restart("hud")

        threading.Thread(target=_run_restart, daemon=True, name="hud-daemon-restart").start()

    @objc.IBAction
    def confirmQuit_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("退出 Penpal HUD？")
        alert.setInformativeText_("HUD 退出后状态栏图标会消失，后台采集 daemon 继续运行。")
        alert.addButtonWithTitle_("退出")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            AppKit.NSApp.terminate_(None)


def run_hud(cfg: Config | None = None) -> None:
    config = cfg or Config.load()
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    delegate = PenpalHUDApp.alloc().initWithConfig_(config)
    app.setDelegate_(delegate)

    def _force_shutdown(sig, frame):
        print("\n[Penpal] 接收到退出信号，清理并关闭...")
        AppKit.NSApp.terminate_(None)
        os._exit(0)  # make sure the terminal gets control back after Ctrl+C

    signal.signal(signal.SIGINT, _force_shutdown)
    signal.signal(signal.SIGTERM, _force_shutdown)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        REFRESH_SEC, delegate, objc.selector(delegate.pollSignals_, signature=b"v@:@"), None, True
    )
    print("Penpal HUD 已启动。")
    app.run()
