from __future__ import annotations

import os
import signal
import AppKit
import objc
from Foundation import NSTimer
from WebKit import WKNavigationActionPolicyAllow, WKNavigationActionPolicyCancel, WKWebView, WKWebViewConfiguration

# 内部模块依赖 (保持原样)
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.config import Config
from keypulse.hud.health import HEALTH_JSON_PATH, health_status_emoji, read_health
from keypulse.hud.monitor_html import build_monitor_html
from keypulse.hud.state import set_today_focus
from keypulse.hud.summary import _status_symbol, build_hud_snapshot
from keypulse.store.db import init_db
from keypulse.store.repository import get_state, insert_raw_event, set_state


HUD_CONTENT_WIDTH = 280.0
HUD_CONTENT_HEIGHT = 320.0

class KeyPulseHUDApp(AppKit.NSObject):
    def initWithConfig_(self, cfg: Config):
        self = objc.super(KeyPulseHUDApp, self).init()
        if self is None: return None
        self.cfg = cfg
        self.health = read_health()
        self.snapshot = build_hud_snapshot(cfg, date_str="today")
        self.capture_status = str(get_state("status") or "running")
        
        # 1. 状态栏 Item
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._update_status_title()
        self.status_item.button().setTarget_(self)
        self.status_item.button().setAction_(objc.selector(self.togglePopover_, signature=b"v@:@"))
        
        # 2. Popover 初始化
        self.popover = AppKit.NSPopover.alloc().init()
        self.popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        self.popover_vc = AppKit.NSViewController.alloc().init()
        self.popover.setContentViewController_(self.popover_vc)
        
        return self

    def _base_status_title(self) -> str:
        return f"{_status_symbol(self.capture_status)} {self.snapshot.effective_count}"

    def _status_title(self) -> str:
        return f"{health_status_emoji(self.health)} · {self._base_status_title()}"

    def _health_label(self) -> str:
        if not isinstance(self.health, dict):
            return "Health: Unknown"
        if health_status_emoji(self.health) == "🟢":
            return "Health: OK"
        alerts = [str(item).strip() for item in list(self.health.get("alerts") or []) if str(item).strip()]
        return f"Health: Alert ({len(alerts)})"

    def _health_message(self) -> tuple[str, str]:
        if not isinstance(self.health, dict):
            return (
                "Health: Unknown",
                f"{HEALTH_JSON_PATH} has not been written yet. M10.2 healthcheck may still be missing or stale.",
            )
        alerts = [str(item).strip() for item in list(self.health.get("alerts") or []) if str(item).strip()]
        if not alerts:
            alerts = ["No alert details were provided."]
        return ("Health: Alert", "\n".join(f"• {item}" for item in alerts))

    def _update_status_title(self):
        self.status_item.button().setTitle_(self._status_title())

    def _health_ok(self) -> bool:
        return isinstance(self.health, dict) and health_status_emoji(self.health) == "🟢"

    def refresh_status(self):
        self.health = read_health()
        self.capture_status = str(get_state("status") or "running")
        self._update_status_title()

    def _refresh_content(self):
        self.snapshot = build_hud_snapshot(self.cfg, date_str="today")
        if self.popover.isShown():
            view = self._build_full_home_view()
            self.popover_vc.setView_(view)
            self.popover.setContentSize_(self._content_size())

    # --- 核心 UI 模块 ---

    def _content_size(self):
        return AppKit.NSMakeSize(HUD_CONTENT_WIDTH, HUD_CONTENT_HEIGHT)

    def _build_full_home_view(self):
        frame = AppKit.NSMakeRect(0.0, 0.0, HUD_CONTENT_WIDTH, HUD_CONTENT_HEIGHT)
        config = WKWebViewConfiguration.alloc().init()
        webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        webview.setNavigationDelegate_(self)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        if hasattr(webview, "setDrawsBackground_"):
            webview.setDrawsBackground_(False)
        html = build_monitor_html(
            self.snapshot,
            capture_status=self.capture_status,
            health_ok=self._health_ok(),
        )
        webview.loadHTMLString_baseURL_(html, None)
        self.webview = webview
        return webview

    def _handle_keypulse_action(self, action: str):
        if action == "save-thought":
            self.saveThought_(None)
        elif action == "set-intent":
            self.setFocus_(None)
        elif action == "toggle-pause":
            self.togglePause_(None)
        elif action == "show-health":
            self.showHealth_(None)
        elif action == "quit":
            self.terminate_(None)

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, _webview, navigation_action, decision_handler):
        url = navigation_action.request().URL()
        if url is not None and str(url.scheme()).lower() == "keypulse":
            host = str(url.host() or "")
            path = str(url.path() or "")
            if host == "action":
                action = path.lstrip("/")
                self._handle_keypulse_action(action)
            decision_handler(WKNavigationActionPolicyCancel)
            return
        decision_handler(WKNavigationActionPolicyAllow)

    # --- Actions (保持逻辑，优化交互) ---

    @objc.IBAction
    def togglePopover_(self, sender):
        if self.popover.isShown():
            self.popover.performClose_(None)
        else:
            self.refresh()
            view = self._build_full_home_view()
            self.popover_vc.setView_(view)
            self.popover.setContentSize_(self._content_size())
            self.popover.showRelativeToRect_ofView_preferredEdge_(sender.bounds(), sender, 1)

    def refresh(self):
        self.refresh_status()
        self._refresh_content()

    @objc.IBAction
    def showHealth_(self, _sender):
        if health_status_emoji(self.health) == "🟢":
            return
        message, informative_text = self._health_message()
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(informative_text)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    @objc.IBAction
    def saveThought_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("💡 记录想法")
        field = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 240, 24))
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            if field.stringValue():
                init_db(self.cfg.db_path_expanded)
                insert_raw_event(normalize_manual_event(text=field.stringValue()))
                self.refresh()

    @objc.IBAction
    def setFocus_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("🎯 设置今日意图")
        field = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 240, 24))
        field.setStringValue_(self.snapshot.today_focus or "")
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("确定")
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            set_today_focus(field.stringValue())
            self.refresh()

    @objc.IBAction
    def togglePause_(self, _sender):
        new_status = "running" if self.capture_status == "paused" else "paused"
        set_state("status", new_status)
        self.refresh()
        self.popover.performClose_(None)

    @objc.IBAction
    def terminate_(self, _sender):
        AppKit.NSApp.terminate_(None)

# --- 启动器 (彻底解决 Ctrl+C 不响应问题) ---

def run_hud(cfg: Config | None = None) -> None:
    config = cfg or Config.load()
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    
    delegate = KeyPulseHUDApp.alloc().initWithConfig_(config)
    app.setDelegate_(delegate)

    # 信号强力桥接：终端按下 Ctrl+C 时强制杀掉进程并返回控制台
    def _force_shutdown(sig, frame):
        print("\n[KeyPulse] 接收到退出信号，清理并关闭...")
        AppKit.NSApp.terminate_(None)
        os._exit(0) # 暴力退出 Python 环境，确保终端状态返回

    signal.signal(signal.SIGINT, _force_shutdown)
    signal.signal(signal.SIGTERM, _force_shutdown)

    # 10 秒自动刷新，仅在 popover 可见时真正拉取最新数据
    def _poll_signals(_timer):
        delegate.refresh_status()
        if delegate.popover.isShown():
            delegate._refresh_content()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        10.0, delegate, objc.selector(_poll_signals, signature=b"v@:@"), None, True
    )

    print("✅ KeyPulse HUD 启动。状态、指标、模式一键直达。")
    app.run()
