"""Preset app -> bucket table for Penpal's own capture streams (D26).

Capture rows (keyboard chunks, window focus, clipboard, AX text, browser URLs) carry an app name /
bundle id but no source default. They are classified by app:

  Financial  banking / payment / brokerage apps
  Work       editors / IDEs, terminals, AI assistants, work notes, office + collaboration tools
  Personal   everything else (browsers stay Personal at app level; unknown apps are Personal)

Matching is case-insensitive against the app name and the process name / bundle id. ASCII keys must
match on token boundaries ("code" matches "Code" and "com.microsoft.VSCode"-style ids split on dots,
not "Barcode"); CJK keys match as substrings. Financial is checked before Work.

Extending: add a key to the tuple of the right category. User bucket rules (policies, mode='bucket')
and `[buckets.app_defaults]` in config.toml always win over this table (see store.buckets).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

FINANCIAL_APPS: tuple[str, ...] = (
    # global payment / banking / brokerage / crypto
    "alipay", "paypal", "venmo", "zelle", "cash app", "wise", "revolut", "monzo", "wallet",
    "bank", "chase", "wells fargo", "citibank", "hsbc", "barclays", "santander", "schwab",
    "fidelity", "vanguard", "etrade", "e*trade", "robinhood", "interactive brokers", "ibkr",
    "td ameritrade", "webull", "coinbase", "binance", "metamask", "ynab", "quickbooks", "quicken",
    "tiger brokers", "futu", "moomoo",
    # CN
    "支付宝", "支付", "银行", "证券", "理财", "基金", "云闪付", "京东金融", "蚂蚁", "同花顺",
    "东方财富", "雪球", "富途", "老虎证券",
)

WORK_APPS: tuple[str, ...] = (
    # editors / IDEs
    "code", "visual studio code", "vscode", "cursor", "windsurf", "xcode", "android studio",
    "intellij", "pycharm", "webstorm", "goland", "clion", "rider", "datagrip", "rubymine",
    "phpstorm", "jetbrains", "zed", "sublime text", "sublime", "neovim", "nvim", "emacs",
    # terminals
    "terminal", "iterm2", "iterm", "warp", "ghostty", "alacritty", "kitty", "wezterm",
    # AI assistants
    "claude", "chatgpt", "lm studio", "lmstudio", "codex", "ollama",
    # notes / knowledge for work
    "obsidian", "notion",
    # office / collaboration
    "microsoft word", "microsoft excel", "microsoft powerpoint", "microsoft teams",
    "com.microsoft.word", "com.microsoft.excel", "com.microsoft.powerpoint",
    "pages", "numbers", "keynote", "wps", "feishu", "lark", "飞书", "dingtalk", "钉钉",
    "slack", "zoom", "zoom.us", "figma", "linear", "jira", "confluence", "asana",
)

PRESET: dict[str, tuple[str, ...]] = {"Financial": FINANCIAL_APPS, "Work": WORK_APPS}
DEFAULT_APP_BUCKET = "Personal"


def _compile(keys: Iterable[str]) -> re.Pattern:
    ascii_keys = sorted((k.lower() for k in keys if k.isascii()), key=len, reverse=True)
    cjk_keys = sorted((k for k in keys if not k.isascii()), key=len, reverse=True)
    parts = []
    if ascii_keys:
        parts.append(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(re.escape(k) for k in ascii_keys))
    if cjk_keys:
        parts.append("|".join(re.escape(k) for k in cjk_keys))
    return re.compile("|".join(parts))


_COMPILED = [(bucket, _compile(keys)) for bucket, keys in PRESET.items()]  # Financial first


def classify_app(app_name: Optional[str], process_name: Optional[str] = None) -> Optional[str]:
    """Preset bucket for an app, or None when no preset entry matches (caller applies the default)."""
    names = [(n or "").strip().lower() for n in (app_name, process_name)]
    for bucket, pattern in _COMPILED:  # Financial first, across both names
        if any(n and pattern.search(n) for n in names):
            return bucket
    return None
