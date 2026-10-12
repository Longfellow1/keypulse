"""Preset website -> bucket table (D28): browser content is bucketed by site, not by browser app.

A browser event (browser_url, or ax_text / clipboard / window / keyboard events whose app is a
browser) takes the bucket of the site it belongs to:

  Financial  banks, payment apps, brokers and exchanges (checked first, so "docs.mybank.com" is Financial)
  Work       code hosting, issue trackers, docs/office suites, AI assistants, cloud consoles, developer docs
  Personal   everything else

Matching is on the host: a key matches the host itself and any subdomain ("github.com" matches
"gist.github.com"). `WORK_HOST_LABELS` additionally treats a host whose first label is "docs" /
"developer" ... as Work. Users edit the table through config `[buckets.site_defaults]` (host suffix ->
bucket); config beats this preset, and user bucket rules (policies) beat both (see store.buckets).
Pure Python, no I/O.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

DEFAULT_SITE_BUCKET = "Personal"

FINANCIAL_HOSTS: tuple[str, ...] = (
    # payment / wallets / exchanges
    "alipay.com", "alipay.cn", "antgroup.com", "paypal.com", "paypal.me", "venmo.com", "wise.com",
    "revolut.com", "coinbase.com", "binance.com", "okx.com", "kraken.com",
    "unionpay.com", "95516.com", "tenpay.com", "pay.weixin.qq.com",
    # US / global banks and brokers
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "citibank.com", "capitalone.com",
    "americanexpress.com", "hsbc.com", "hsbc.com.cn", "hsbc.com.hk", "barclays.co.uk", "santander.com",
    "schwab.com", "fidelity.com", "vanguard.com", "robinhood.com", "interactivebrokers.com", "ibkr.com",
    "etrade.com", "webull.com", "tdameritrade.com", "tastytrade.com", "sofi.com", "ally.com",
    # CN banks
    "icbc.com.cn", "ccb.com", "boc.cn", "abchina.com", "abchina.com.cn", "bankcomm.com", "cmbchina.com",
    "psbc.com", "spdb.com.cn", "cebbank.com", "cib.com.cn", "pingan.com", "citicbank.com", "cmbc.com.cn",
    "hxb.com.cn", "cgbchina.com.cn", "bosc.cn",
    # CN / HK brokers and market apps
    "futunn.com", "futu5.com", "moomoo.com", "tigerbrokers.com", "itiger.com", "eastmoney.com",
    "10jqka.com.cn", "xueqiu.com", "htsc.com.cn", "gtja.com", "guosen.com.cn", "cicc.com.cn", "citics.com",
    "cmschina.com", "swhysc.com", "csc108.com", "gf.com.cn", "ebscn.com", "dfzq.com.cn",
)

WORK_HOSTS: tuple[str, ...] = (
    # Feishu / Lark
    "feishu.cn", "larksuite.com", "larkoffice.com", "feishu.net",
    # code hosting / issue trackers / design / collaboration
    "github.com", "githubusercontent.com", "gitlab.com", "gitee.com", "bitbucket.org", "atlassian.net",
    "atlassian.com", "notion.so", "notion.site", "figma.com", "linear.app", "asana.com", "miro.com",
    "airtable.com", "slack.com",
    # AI assistants / model hubs / research
    "chatgpt.com", "chat.openai.com", "openai.com", "claude.ai", "anthropic.com", "doubao.com", "kimi.com",
    "kimi.ai", "moonshot.cn", "deepseek.com", "huggingface.co", "arxiv.org", "gemini.google.com",
    "perplexity.ai", "yuanbao.tencent.com", "tongyi.aliyun.com", "qianwen.com",
    # Q&A / docs / package indexes
    "stackoverflow.com", "stackexchange.com", "readthedocs.io", "readthedocs.org",
    "developer.mozilla.org", "pkg.go.dev", "pypi.org", "npmjs.com", "crates.io",
    # cloud consoles
    "console.aws.amazon.com", "aws.amazon.com", "aliyun.com", "cloud.tencent.com", "volcengine.com",
    "portal.azure.com", "console.cloud.google.com", "cloud.google.com", "vercel.com", "dash.cloudflare.com",
)

# A host whose first label is one of these is a documentation / developer site ("docs.python.org"),
# or a self-hosted GitLab ("gitlab.corp.example").
WORK_HOST_LABELS: tuple[str, ...] = ("docs", "doc", "developer", "developers", "devdocs", "gitlab")

# "mybank.cn", "ebank.example.com": any host containing "bank" errs towards Financial.
_BANK_HOST_RE = re.compile(r"bank", re.IGNORECASE)

# ── browsers ────────────────────────────────────────────────────────────────────────────────
# family -> (case-folded app names, case-folded bundle id prefixes)
_BROWSERS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "chrome": (frozenset({"google chrome", "chrome", "google chrome canary", "google chrome beta"}), ("com.google.chrome",)),
    "safari": (frozenset({"safari", "safari technology preview"}), ("com.apple.safari",)),
    "edge": (frozenset({"microsoft edge", "edge"}), ("com.microsoft.edgemac",)),
    "arc": (frozenset({"arc"}), ("company.thebrowser.browser",)),
    "firefox": (frozenset({"firefox", "firefox developer edition", "nightly"}), ("org.mozilla.firefox",)),
    "brave": (frozenset({"brave browser", "brave"}), ("com.brave.browser",)),
    "chromium": (frozenset({"chromium"}), ("org.chromium.chromium",)),
    "vivaldi": (frozenset({"vivaldi"}), ("com.vivaldi.vivaldi",)),
    "opera": (frozenset({"opera"}), ("com.operasoftware.opera",)),
    "orion": (frozenset({"orion"}), ("com.kagi.kagimacos",)),
}


def browser_family(app_name: Optional[str], process_name: Optional[str] = None) -> Optional[str]:
    """Canonical browser family ("chrome", "safari", ...) of an app name / bundle id, or None."""
    names = [(n or "").strip().casefold() for n in (app_name, process_name)]
    for family, (app_names, bundle_prefixes) in _BROWSERS.items():
        for n in names:
            if n and (n in app_names or n.startswith(bundle_prefixes)):
                return family
    return None


# ── hosts ───────────────────────────────────────────────────────────────────────────────────
def host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    text = str(url).strip()
    if "://" not in text:
        text = "//" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def _suffix_match(host: str, key: str) -> bool:
    return host == key or host.endswith("." + key)


def normalize_site_key(key: str) -> str:
    """Config key -> bare lower-case host suffix ("*.Notion.so", "https://notion.so/x" -> "notion.so")."""
    text = (key or "").strip().lower()
    if "://" in text:
        text = host_of(text) or text
    return text.lstrip("*.").strip("/")


def classify_host(host: Optional[str], overrides: Optional[dict[str, str]] = None) -> Optional[str]:
    """Bucket for a host: config overrides (longest suffix wins) > preset table. None = no entry
    matched (the caller falls back to the app-level bucket, i.e. Personal for a browser)."""
    if not host:
        return None
    best: Optional[tuple[int, str]] = None
    for key, bucket in (overrides or {}).items():
        if key and _suffix_match(host, key) and (best is None or len(key) > best[0]):
            best = (len(key), bucket)
    if best:
        return best[1]
    if any(_suffix_match(host, k) for k in FINANCIAL_HOSTS) or _BANK_HOST_RE.search(host):
        return "Financial"
    if any(_suffix_match(host, k) for k in WORK_HOSTS) or host.split(".", 1)[0] in WORK_HOST_LABELS:
        return "Work"
    return None


def classify_url(url: Optional[str], overrides: Optional[dict[str, str]] = None) -> Optional[str]:
    return classify_host(host_of(url), overrides)
