from __future__ import annotations

import os
import signal
import subprocess
import AppKit
import objc
from Foundation import NSTimer, NSURL
from WebKit import WKNavigationActionPolicyAllow, WKNavigationActionPolicyCancel, WKWebView, WKWebViewConfiguration

# 内部模块依赖 (保持原样)
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.config import Config
from keypulse.hud.health import HEALTH_JSON_PATH, health_status_emoji, read_health
from keypulse.hud.monitor_html import build_monitor_html
from keypulse.hud.state import set_today_focus
from keypulse.hud.summary import build_hud_snapshot
from keypulse.store.db import init_db
from keypulse.store.repository import get_state, insert_raw_event, set_state


HUD_CONTENT_WIDTH = 365.0
HUD_CONTENT_HEIGHT_FALLBACK = 440.0  # used until WebView reports actual content height
HUD_CONTENT_HEIGHT_MIN = 200.0
HUD_CONTENT_HEIGHT_MAX = 900.0
DAEMON_LAUNCHD_LABEL = "com.keypulse.daemon"

class KeyPulseHUDApp(AppKit.NSObject):
    def initWithConfig_(self, cfg: Config):
        self = objc.super(KeyPulseHUDApp, self).init()
        if self is None: return None
        self.cfg = cfg
        self.health = read_health()
        self.capture_status = str(get_state("status") or "running")
        self.snapshot = build_hud_snapshot(
            cfg,
            date_str="today",
            capture_status=self.capture_status,
            health_ok=self._compute_health_ok(),
        )

        # 1. 状态栏 Item
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._install_status_icon()
        self.status_item.button().setTarget_(self)
        self.status_item.button().setAction_(objc.selector(self.togglePopover_, signature=b"v@:@"))
        
        # 2. Popover 初始化
        self.popover = AppKit.NSPopover.alloc().init()
        self.popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        self.popover_vc = AppKit.NSViewController.alloc().init()
        self.popover.setContentViewController_(self.popover_vc)
        
        return self

    def _install_status_icon(self) -> None:
        """状态栏 icon：本本 + 笔（SF Symbol，模板图标，自动适配深浅色）。"""
        button = self.status_item.button()
        button.setTitle_("")
        image = None
        if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "square.and.pencil", "KeyPulse"
            )
        if image is None:
            image = AppKit.NSImage.imageNamed_("NSActionTemplate")
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setImagePosition_(AppKit.NSImageOnly)

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

    def _compute_health_ok(self) -> bool:
        return isinstance(self.health, dict) and health_status_emoji(self.health) == "🟢"

    def _health_ok(self) -> bool:
        return self._compute_health_ok()

    def refresh_status(self):
        self.health = read_health()
        self.capture_status = str(get_state("status") or "running")

    def _refresh_content(self):
        self.snapshot = build_hud_snapshot(
            self.cfg,
            date_str="today",
            capture_status=self.capture_status,
            health_ok=self._compute_health_ok(),
        )
        if self.popover.isShown():
            self._measured_height = None
            view = self._build_full_home_view()
            self.popover_vc.setView_(view)

    # --- 核心 UI 模块 ---

    def _content_size(self):
        height = getattr(self, "_measured_height", None) or HUD_CONTENT_HEIGHT_FALLBACK
        return AppKit.NSMakeSize(HUD_CONTENT_WIDTH, float(height))

    def _build_full_home_view(self):
        height = getattr(self, "_measured_height", None) or HUD_CONTENT_HEIGHT_FALLBACK
        frame = AppKit.NSMakeRect(0.0, 0.0, HUD_CONTENT_WIDTH, float(height))
        config = WKWebViewConfiguration.alloc().init()
        webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        webview.setNavigationDelegate_(self)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        if hasattr(webview, "setDrawsBackground_"):
            webview.setDrawsBackground_(False)
        # Hide WKWebView's own scrollers — we resize the popover to fit content.
        try:
            scroll_view = webview.scrollView() if hasattr(webview, "scrollView") else None
            if scroll_view is not None:
                scroll_view.setHasVerticalScroller_(False)
                scroll_view.setHasHorizontalScroller_(False)
        except Exception:
            pass
        html = build_monitor_html(
            self.snapshot,
            capture_status=self.capture_status,
            health_ok=self._health_ok(),
        )
        webview.loadHTMLString_baseURL_(html, None)
        self.webview = webview
        return webview

    def webView_didFinishNavigation_(self, webview, _navigation):
        # First measurement after the HTML has rendered.
        self._measure_and_resize()

    def _measure_and_resize(self) -> None:
        if not hasattr(self, "webview") or self.webview is None:
            return
        def _on_result(value, _err):
            if value is None:
                return
            try:
                height = float(value)
            except (TypeError, ValueError):
                return
            height = max(HUD_CONTENT_HEIGHT_MIN, min(height, HUD_CONTENT_HEIGHT_MAX))
            current = getattr(self, "_measured_height", None)
            if current is not None and abs(current - height) < 1.0:
                return
            self._measured_height = height
            if self.popover.isShown():
                size = self._content_size()
                self.popover_vc.setPreferredContentSize_(size)
                self.popover.setContentSize_(size)
        try:
            self.webview.evaluateJavaScript_completionHandler_(
                "Math.ceil(document.documentElement.scrollHeight)",
                _on_result,
            )
        except Exception:
            pass

    def _handle_keypulse_action(self, action: str, params: dict[str, str] | None = None):
        params = params or {}
        if action == "save-thought":
            self.saveThought_(None)
        elif action in {"set-intent", "edit-today-focus"}:
            self.setFocus_(None)
        elif action == "save-today-focus":
            self._save_today_focus(params.get("v", ""))
        elif action == "toggle-pause":
            self.togglePause_(None)
        elif action == "restart-daemon":
            self.restartDaemon_(None)
        elif action == "show-health":
            self.showHealth_(None)
        elif action == "open-url":
            url_str = params.get("u", "")
            if url_str:
                ns_url = NSURL.URLWithString_(url_str)
                if ns_url is not None:
                    AppKit.NSWorkspace.sharedWorkspace().openURL_(ns_url)
        elif action == "quit":
            self.confirmQuit_(None)
        elif action == "close-popover":
            if self.popover.isShown():
                self.popover.performClose_(None)
        elif action == "resize":
            try:
                height = float(params.get("h", "0"))
            except ValueError:
                return
            if height <= 0:
                return
            height = max(HUD_CONTENT_HEIGHT_MIN, min(height, HUD_CONTENT_HEIGHT_MAX))
            self._measured_height = height
            if self.popover.isShown():
                size = self._content_size()
                self.popover_vc.setPreferredContentSize_(size)
                self.popover.setContentSize_(size)

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, _webview, navigation_action, decision_handler):
        url = navigation_action.request().URL()
        if url is not None and str(url.scheme()).lower() == "keypulse":
            host = str(url.host() or "")
            path = str(url.path() or "")
            if host == "action":
                action = path.lstrip("/")
                params = self._parse_query(url)
                self._handle_keypulse_action(action, params)
            decision_handler(WKNavigationActionPolicyCancel)
            return
        decision_handler(WKNavigationActionPolicyAllow)

    @staticmethod
    def _parse_query(url) -> dict[str, str]:
        from urllib.parse import parse_qsl
        query = str(url.query() or "")
        return dict(parse_qsl(query, keep_blank_values=True))

    def _save_today_focus(self, value: str):
        set_today_focus(value)
        self.refresh()

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
        alert.setMessageText_("✦ 今日重要的事儿")
        alert.setInformativeText_("写下今天最想完成的一件事")
        field = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 280, 24))
        field.setStringValue_(self.snapshot.today_focus or "")
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("取消")
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
    def restartDaemon_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("重启 KeyPulse daemon？")
        alert.setInformativeText_("会短暂中断采集，几秒后自动恢复")
        alert.addButtonWithTitle_("重启")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            return
        target = f"gui/{os.getuid()}/{DAEMON_LAUNCHD_LABEL}"
        try:
            subprocess.Popen(
                ["launchctl", "kickstart", "-k", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            err = AppKit.NSAlert.alloc().init()
            err.setMessageText_("重启失败")
            err.setInformativeText_(str(exc))
            err.addButtonWithTitle_("OK")
            err.runModal()

    @objc.IBAction
    def terminate_(self, _sender):
        AppKit.NSApp.terminate_(None)

    @objc.IBAction
    def confirmQuit_(self, _sender):
        self.popover.performClose_(None)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("退出 KeyPulse HUD？")
        alert.setInformativeText_("HUD 退出后状态栏图标会消失，后台采集 daemon 继续运行。")
        alert.addButtonWithTitle_("退出")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
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
