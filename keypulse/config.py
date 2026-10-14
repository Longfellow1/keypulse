from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AppConfig(BaseModel):
    db_path: str = "~/.keypulse/keypulse.db"
    log_path: str = "~/.keypulse/keypulse.log"
    flush_interval_sec: int = 5
    # D32: deprecated and ignored. Kept only so old config files still parse; nothing is deleted automatically.
    retention_days: int = 30
    timezone: str | None = None  # IANA name (e.g. "Asia/Shanghai"); None = follow system tz


class WatchersConfig(BaseModel):
    window: bool = True
    idle: bool = True
    clipboard: bool = True
    manual: bool = True
    keyboard_chunk: bool = True
    browser: bool = False
    browser_url: bool = False
    ax_text: bool = False
    ocr: bool = False


class IdleConfig(BaseModel):
    threshold_sec: int = 180


class ClipboardConfig(BaseModel):
    max_text_length: int = 2000
    dedup_window_sec: int = 600


class AXTextConfig(BaseModel):
    poll_interval_sec: float = 1.0


class KeyboardChunkConfig(BaseModel):
    silence_sec: float = 2.0
    # D27: a flush after ~20s of continuous typing keeps chunks near sentence length
    # (2s chunks were too short for any block-level filter to work on).
    force_flush_sec: float = 20.0
    store_text: bool = True


class BrowserConfig(BaseModel):
    poll_interval_sec: float = 1.0
    supported_browsers: list[str] = Field(
        default_factory=lambda: [
            "Safari",
            "Google Chrome",
            "Arc",
            "Brave Browser",
            "Microsoft Edge",
        ]
    )


class BrowserURLConfig(BaseModel):
    poll_interval_sec: float = 3.0
    supported_browsers: Literal["auto"] | list[str] | tuple[str, ...] = Field(
        default="auto"
    )
    emit_on_url_change_only: bool = True


class BrowserHistoryConfig(BaseModel):
    enabled: bool = False
    poll_interval_sec: float = 300.0
    copy_to_cache: bool = True
    browsers: list[str] = Field(
        default_factory=lambda: [
            "Safari",
            "Google Chrome",
            "Arc",
            "Microsoft Edge",
            "Brave Browser",
            "Firefox",
        ]
    )


class OCRConfig(BaseModel):
    provider: Literal["vision_native"] = "vision_native"
    # === OCR watcher 已下线 2026-05-14 ===
    # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
    # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
    # 历史 raw_events 中 ocr_text_capture 数据保留可读
    # window_switch_delay_sec: float = 0.8
    # stable_interval_sec: float = 10.0
    # keyboard_quiet_sec: float = 2.0


def _default_title_deny():
    from keypulse.capture.hygiene import DEFAULT_TITLE_DENY_PATTERNS

    return DEFAULT_TITLE_DENY_PATTERNS


class PrivacyConfig(BaseModel):
    redact_emails: bool = True
    redact_phones: bool = True
    redact_tokens: bool = True
    drop_terminal_text: bool = True
    url_deny_hosts: list[str] = Field(
        default_factory=lambda: [
            "apple.com",
            "appleid.apple.com",
            "xiaohongshu.com",
            "weibo.com",
            "bilibili.com",
            "zhihu.com",
            "douyin.com",
            "xhs.com",
        ]
    )
    camera_scene_pause: bool = True
    blacklist_bundle_ids: list[str] = Field(
        default_factory=lambda: [
            "com.agilebits.onepassword7",
            "com.agilebits.onepassword-ios",
            "com.bitwarden.desktop",
            "com.dashlane.mac",
            "com.lastpass.lp",
            "com.apple.keychainaccess",
            "com.microsoft.authenticator",
            "com.tencent.wechat",
            "com.tencent.qq",
            "org.telegram.desktop",
            "org.signal.signal-desktop",
            "com.whatsapp.messenger",
            "com.apple.Messages",
            # Slack's macOS bundle id (was "com.tinyspk.slack", a typo).
            "com.tinyspeck.slackmacgap",
            "com.hnc.Discord",
            "com.tencent.wxwork",
            "com.alibaba.dingtalk",
            # Verified from locally installed Lark.app / Telegram.app Info.plist (CFBundleIdentifier);
            # the old feishu id was a guess. DingTalk's mac id is from public listings, not verified here.
            "com.alibaba.DingTalkMac",
            "com.electron.lark",
            "com.bytedance.feishu",
            "ru.keepcoder.Telegram",
            "com.tencent.xinWeChat",
            "jp.naver.line",
            "com.kakao.talk",
            "com.icbc.mobile",
            "com.bankofchina.mobilebank",
            "com.wf.wellsfargomobile",
            "com.chase.mobile",
            "com.bankofamerica.mobile",
            "com.dbs.dbsmbanking",
            "com.robinhoodinc.robinhood",
            "com.schwab.mobile",
            "com.etrade.mobile",
            "com.crypto.app",
            "io.metamask.mobile",
            "com.ledger.live",
            "com.epic.mychart",
            "com.headspace.meditation",
            "com.calm.ios",
        ]
    )
    blacklist_patterns: list[str] = Field(default_factory=lambda: ["com.tencent.*"])
    # D27: events without a bundle id (clipboard) are matched by app name, case-insensitive substring.
    # D27: regexes (case-insensitive) matched against window titles; a hit makes the event
    # metadata-only. Seeded from the login-context keywords (sign in, password, 登录, ...).
    title_deny_patterns: list[str] = Field(default_factory=lambda: list(_default_title_deny()))
    blacklist_app_names: list[str] = Field(
        default_factory=lambda: [
            "微信", "WeChat", "企业微信", "钉钉", "DingTalk", "飞书", "Lark", "Feishu",
            "1Password", "Keychain Access", "钥匙串", "密码",
        ]
    )
    # Chat apps (keypulse.capture.chat_apps) are excluded from capture by default. Listing one here
    # opts it in: its rows are stored and bucketed as Private. Keys or listed names, e.g. ["WeChat", "Slack"].
    chat_apps_opt_in: list[str] = Field(default_factory=list)

    @field_validator("chat_apps_opt_in")
    @classmethod
    def _chat_apps_opt_in_known(cls, value: list[str]) -> list[str]:
        from keypulse.capture.chat_apps import normalize_opt_ins

        return normalize_opt_ins(value)


class MarkdownVaultSourceConfig(BaseModel):
    extra_roots: list[str] = Field(default_factory=list)


class SourcesSchedulerConfig(BaseModel):
    enabled: bool = True
    poll_interval_sec: int = 600
    debounce_sec: int = 300


class SourcesConfig(BaseModel):
    markdown_vault: MarkdownVaultSourceConfig = Field(default_factory=MarkdownVaultSourceConfig)
    scheduler: SourcesSchedulerConfig = Field(default_factory=SourcesSchedulerConfig)


class BucketsConfig(BaseModel):
    # Overrides of the built-in per-source default bucket, e.g. {"markdown_vault": "Work"}.
    source_defaults: dict[str, str] = Field(default_factory=dict)
    # Capture-stream app overrides (D26), e.g. {"Obsidian": "Personal"}; case-insensitive substring
    # of the app / process name. Beats the preset app table, loses to user bucket rules.
    app_defaults: dict[str, str] = Field(default_factory=dict)
    # Website overrides for browser content (D28), e.g. {"notion.so": "Personal", "corp.example.com": "Work"};
    # host-suffix match. Beats the preset site table (store.site_categories), loses to user bucket rules.
    site_defaults: dict[str, str] = Field(default_factory=dict)


class PolicyConfig(BaseModel):
    scope_type: str
    scope_value: str
    mode: str = "allow"  # allow|deny|metadata-only|redact|truncate (ignored when `bucket` is set)
    bucket: str | None = None  # bucket rule: Work|Personal|Private|Financial (scope_type app|source)
    enabled: bool = True
    priority: int = 100


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    watchers: WatchersConfig = Field(default_factory=WatchersConfig)
    idle: IdleConfig = Field(default_factory=IdleConfig)
    clipboard: ClipboardConfig = Field(default_factory=ClipboardConfig)
    ax_text: AXTextConfig = Field(default_factory=AXTextConfig)
    keyboard_chunk: KeyboardChunkConfig = Field(default_factory=KeyboardChunkConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    browser_url: BrowserURLConfig = Field(default_factory=BrowserURLConfig)
    browser_history: BrowserHistoryConfig = Field(default_factory=BrowserHistoryConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    buckets: BucketsConfig = Field(default_factory=BucketsConfig)
    policies: list[PolicyConfig] = Field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        """Load config from ~/.keypulse/config.toml or ./config.toml, falling back to defaults."""
        from keypulse.utils.env import load_dotenv_quiet
        load_dotenv_quiet()
        paths = [
            Path.home() / ".keypulse" / "config.toml",
            Path("config.toml"),
        ]
        for p in paths:
            if p.exists():
                with open(p, "rb") as f:
                    data = tomllib.load(f)
                return cls.model_validate(data)
        return cls()

    @property
    def db_path_expanded(self) -> Path:
        return Path(self.app.db_path).expanduser()

    @property
    def log_path_expanded(self) -> Path:
        return Path(self.app.log_path).expanduser()
