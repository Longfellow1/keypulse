"""Chat apps (D19, issue #26): excluded from content capture by default.

A chat app's window titles, keyboard input, AX text and clipboard are never stored unless the user
opts that app in explicitly, via `[privacy] chat_apps_opt_in = ["WeChat", ...]` in config.toml.
An opted-in chat app's rows are bucketed as Private (store.buckets.classify_capture_app).

Pure Python, no imports from keypulse: config.py, the capture manager and the bucket layer all use it.
Identity is exact (bundle id or whole app name, case-insensitive), so a miss fails closed: the app is
then treated as a chat app only if it is listed here, and an unlisted app keeps the existing
blacklist behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

PRIVATE_BUCKET = "Private"


@dataclass(frozen=True)
class ChatApp:
    key: str  # canonical name used in config.toml
    bundle_ids: tuple[str, ...]
    names: tuple[str, ...]  # app / process names; the key itself always matches too


# Bundle ids already in config.PrivacyConfig.blacklist_bundle_ids are reused; ids not verified on this
# machine are marked. Add new chat apps here only.
CHAT_APPS: tuple[ChatApp, ...] = (
    ChatApp("WeChat", ("com.tencent.xinWeChat", "com.tencent.wechat"), ("微信", "WeChat")),
    ChatApp("WeCom", ("com.tencent.wxwork",), ("企业微信", "WeCom", "WXWork")),
    ChatApp("QQ", ("com.tencent.qq",), ("QQ",)),
    ChatApp("Telegram", ("ru.keepcoder.Telegram", "org.telegram.desktop"), ("Telegram",)),
    ChatApp("Slack", ("com.tinyspeck.slackmacgap",), ("Slack",)),
    ChatApp("Discord", ("com.hnc.Discord",), ("Discord",)),
    ChatApp("Messages", ("com.apple.Messages", "com.apple.MobileSMS"), ("Messages", "信息")),
    # net.whatsapp.WhatsApp is the mac app id from public listings, not verified on this machine.
    ChatApp("WhatsApp", ("com.whatsapp.messenger", "net.whatsapp.WhatsApp"), ("WhatsApp",)),
    ChatApp("Feishu", ("com.electron.lark", "com.bytedance.feishu"), ("Lark", "Feishu", "飞书")),
    ChatApp("DingTalk", ("com.alibaba.DingTalkMac", "com.alibaba.dingtalk"), ("DingTalk", "钉钉")),
    ChatApp("Signal", ("org.signal.signal-desktop",), ("Signal",)),
    ChatApp("LINE", ("jp.naver.line",), ("LINE",)),
    ChatApp("KakaoTalk", ("com.kakao.talk",), ("KakaoTalk",)),
)

_BY_BUNDLE: dict[str, str] = {}
_BY_NAME: dict[str, str] = {}
for _app in CHAT_APPS:
    for _bid in _app.bundle_ids:
        _BY_BUNDLE[_bid.casefold()] = _app.key
    for _name in (_app.key, *_app.names):
        _BY_NAME[_name.casefold()] = _app.key
del _app, _bid, _name


def chat_app_key(app_name: Optional[str], process_name: Optional[str] = None) -> Optional[str]:
    """Canonical chat app key for an event's app / process, or None when it is not a chat app."""
    for value in (process_name, app_name):
        folded = str(value or "").strip().casefold()
        if folded:
            key = _BY_BUNDLE.get(folded) or _BY_NAME.get(folded)
            if key:
                return key
    return None


def normalize_opt_ins(values: Iterable[str]) -> list[str]:
    """Canonical keys for opt-in entries (a key or any listed name, case-insensitive); ValueError if unknown."""
    found: list[str] = []
    for raw in values:
        key = _BY_NAME.get(str(raw).strip().casefold())
        if key is None:
            known = ", ".join(app.key for app in CHAT_APPS)
            raise ValueError(f"unknown chat app {raw!r} in privacy.chat_apps_opt_in; choose from {known}")
        if key not in found:
            found.append(key)
    return found
