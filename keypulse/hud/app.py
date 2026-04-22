from __future__ import annotations

import os
import signal
import AppKit
import objc
from Foundation import NSTimer

# 内部模块依赖 (保持原样)
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.config import Config
from keypulse.hud.health import HEALTH_JSON_PATH, health_status_emoji, read_health
from keypulse.hud.state import set_hud_mode, set_today_focus
from keypulse.hud.summary import _status_symbol, build_hud_snapshot
from keypulse.store.db import init_db
from keypulse.store.repository import get_state, insert_raw_event, set_state

def _truncate(text: str, limit: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit: return value
    return value[: limit - 1].rstrip() + "…"


def _signal_icon(source_key: str) -> str:
    return {
        "manual": "✍️",
        "clipboard": "📋",
        "ax_text": "👁",
        "keyboard_chunk": "⌨️",
        "ocr_text": "🔍",
        "window": "🪟",
        "browser_tab": "🌐",
    }.get(source_key, "•")


def _delta_prefix(delta: int | None) -> str:
    if delta is None:
        return "—"
    if delta == 0:
        return "="
    if delta > 0:
        return f"↑{delta}"
    return f"↓{abs(delta)}"


def _format_metric(label: str, value: int, delta: int | None) -> str:
    prefix = _delta_prefix(delta)
    if prefix == "—":
        return f"{label}: {value} —"
    return f"{label}: {value} {prefix} vs 昨日"

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

    def refresh_status(self):
        self.health = read_health()
        self.capture_status = str(get_state("status") or "running")
        self._update_status_title()

    def _refresh_content(self):
        self.snapshot = build_hud_snapshot(self.cfg, date_str="today")
        if self.popover.isShown():
            view = self._build_full_home_view()
            self.popover_vc.setView_(view)
            self.popover.setContentSize_(view.fittingSize())

    # --- 基础 UI 工厂 (强制禁用 AutoresizingMask，彻底防重叠) ---

    def _make_label(self, text, size=13, bold=False, color=None, align=0):
        label = AppKit.NSTextField.labelWithString_(text)
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, AppKit.NSFontWeightBold if bold else AppKit.NSFontWeightRegular))
        label.setTextColor_(color or AppKit.NSColor.labelColor())
        label.setAlignment_(align)
        label.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return label

    def _make_stack(self, vertical=True, spacing=10):
        stack = AppKit.NSStackView.alloc().init()
        stack.setOrientation_(1 if vertical else 0)
        stack.setSpacing_(spacing)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return stack

    # --- 核心 UI 模块 ---

    def _build_full_home_view(self):
        # 根视图：强制宽度，高度自适应
        root_stack = self._make_stack(vertical=True, spacing=18)
        root_stack.setEdgeInsets_((20, 20, 20, 20))
        root_stack.widthAnchor().constraintEqualToConstant_(320.0).setActive_(True)

        # 0. 健康状态入口
        health_row = self._make_stack(vertical=False, spacing=8)
        health_row.addArrangedSubview_(
            AppKit.NSButton.buttonWithTitle_target_action_(self._health_label(), self, "showHealth:")
        )
        health_row.addArrangedSubview_(AppKit.NSView.alloc().init())
        root_stack.addArrangedSubview_(health_row)

        # 1. 状态头部 (Header)
        header = self._make_stack(vertical=False, spacing=8)
        is_running = self.capture_status != "paused"
        dot = self._make_label("●", size=14, color=AppKit.NSColor.systemGreenColor() if is_running else AppKit.NSColor.systemOrangeColor())
        header.addArrangedSubview_(dot)
        header.addArrangedSubview_(self._make_label(f"KeyPulse {self.snapshot.mode_label}", size=14, bold=True))
        root_stack.addArrangedSubview_(header)

        # 2. 今日看板 (Metrics) - 采用 2x2 网格
        metrics_stack = self._make_stack(vertical=True, spacing=8)
        for row_data in [
            (
                _format_metric("有效", self.snapshot.effective_count, self.snapshot.effective_count_delta_vs_yesterday),
                _format_metric("过滤", self.snapshot.filtered_count, self.snapshot.filtered_count_delta_vs_yesterday),
            ),
            (
                _format_metric("主题", self.snapshot.theme_count, self.snapshot.theme_count_delta_vs_yesterday),
                _format_metric("标记", self.snapshot.manual_marked_count, self.snapshot.manual_marked_count_delta_vs_yesterday),
            )
        ]:
            row = self._make_stack(vertical=False, spacing=10)
            row.setDistribution_(AppKit.NSStackViewDistributionFillEqually)
            for item in row_data:
                label = self._make_label(item, size=11, align=2)
                row.addArrangedSubview_(label)
            metrics_stack.addArrangedSubview_(row)
        root_stack.addArrangedSubview_(metrics_stack)

        # 3. 重点流摘要 (Signals)
        if self.snapshot.top_signals:
            root_stack.addArrangedSubview_(self._make_label("最新智能捕捉", size=11, bold=True, color=AppKit.NSColor.secondaryLabelColor()))
            for sig in self.snapshot.top_signals[:3]:
                title = _truncate(f"{_signal_icon(str(sig.get('source_key') or ''))} {sig['title']}", 46)
                sig_label = self._make_label(f"{title}\n{sig['reason']}", size=12)
                root_stack.addArrangedSubview_(sig_label)

        # 4. 模式切换按钮
        mode_row = self._make_stack(vertical=False, spacing=5)
        mode_row.setDistribution_(AppKit.NSStackViewDistributionFillEqually)
        for m_id, m_name in {"standard": "标准", "focus": "专注", "sensitive": "高敏"}.items():
            btn = AppKit.NSButton.buttonWithTitle_target_action_(m_name, self, "changeMode:")
            btn.setRepresentedObject_(m_id)
            btn.setBezelStyle_(AppKit.NSBezelStyleTexturedRounded)
            mode_row.addArrangedSubview_(btn)
        root_stack.addArrangedSubview_(mode_row)

        # 5. 底部动作区
        action_row = self._make_stack(vertical=False, spacing=10)
        action_row.addArrangedSubview_(AppKit.NSButton.buttonWithTitle_target_action_("💡 保存想法", self, "saveThought:"))
        action_row.addArrangedSubview_(AppKit.NSButton.buttonWithTitle_target_action_("🎯 设意图", self, "setFocus:"))
        root_stack.addArrangedSubview_(action_row)

        # 6. 系统控制
        footer = self._make_stack(vertical=False, spacing=0)
        pause_title = "恢复" if not is_running else "暂停 30m"
        footer.addArrangedSubview_(AppKit.NSButton.buttonWithTitle_target_action_(pause_title, self, "togglePause:"))
        footer.addArrangedSubview_(AppKit.NSView.alloc().init())
        footer.addArrangedSubview_(AppKit.NSButton.buttonWithTitle_target_action_( "退出", self, "terminate:"))
        root_stack.addArrangedSubview_(footer)

        # 强制同步布局尺寸给容器
        root_stack.layoutSubtreeIfNeeded()
        return root_stack

    # --- Actions (保持逻辑，优化交互) ---

    @objc.IBAction
    def togglePopover_(self, sender):
        if self.popover.isShown():
            self.popover.performClose_(None)
        else:
            self.refresh()
            view = self._build_full_home_view()
            self.popover_vc.setView_(view)
            # 关键：手动同步内容尺寸
            self.popover.setContentSize_(view.fittingSize())
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
    def changeMode_(self, sender):
        set_hud_mode(sender.representedObject())
        self.refresh()
        self.popover.performClose_(None)

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
