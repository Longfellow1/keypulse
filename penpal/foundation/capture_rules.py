"""Capture names and matching rules shared by capture and persistence code."""

from __future__ import annotations

import re


SYSTEM_OVERLAY_APPS = frozenset({
    "loginwindow", "ScreenSaverEngine", "Dock",
    "Notification Center", "Control Center",
    # First-run / macOS setup overlay (en, zh-Hans, process name, bundle ids): never a real app.
    "Setup Assistant", "SetupAssistant", "设置助理", "com.apple.SetupAssistant",
    "com.apple.loginwindow",
})

LOGIN_KEYWORDS = (
    "loginwindow", "password", "verification", "登录", "验证码",
    "1password", "keychain", "lastpass", "bitwarden",
    "sign in", "sign-in", "signin",
)

DEFAULT_TITLE_DENY_PATTERNS: tuple[str, ...] = tuple(re.escape(k) for k in LOGIN_KEYWORDS)

APP_CHROME_PLACEHOLDERS: tuple[str, ...] = (
    "问问 ChatGPT", "发消息或按住空格说话", "Message ChatGPT", "Ask anything", "有问题，尽管问",
    "输入消息", "Send a message", "Ask Claude", "Message Claude", "Reply to Claude",
    "给 ChatGPT 发送消息", "问点什么", "Ask a follow-up", "Type a message",
    "Search or ask a question", "Message Gemini", "Ask Gemini",
)

_PLACEHOLDER_TRIM = " \t\r\n…。.!！"
_PLACEHOLDERS_FOLDED = frozenset(
    value.casefold().strip(_PLACEHOLDER_TRIM) for value in APP_CHROME_PLACEHOLDERS
)
_OVERLAY_FOLDED = frozenset(name.casefold() for name in SYSTEM_OVERLAY_APPS)
_OVERLAY_BUNDLE_PREFIXES = ("com.apple.setupassistant",)


def is_system_overlay(app_name: str | None, process_name: str | None = None) -> bool:
    """loginwindow / Setup Assistant / Dock & co: the foreground app is a system overlay."""
    for value in (app_name, process_name):
        folded = str(value or "").strip().casefold()
        if folded and (folded in _OVERLAY_FOLDED or folded.startswith(_OVERLAY_BUNDLE_PREFIXES)):
            return True
    return False


def is_placeholder_text(content: str | None) -> bool:
    return str(content or "").strip().casefold().strip(_PLACEHOLDER_TRIM) in _PLACEHOLDERS_FOLDED
