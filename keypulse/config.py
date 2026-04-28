from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    db_path: str = "~/.keypulse/keypulse.db"
    log_path: str = "~/.keypulse/keypulse.log"
    flush_interval_sec: int = 5
    retention_days: int = 30


class WatchersConfig(BaseModel):
    window: bool = True
    idle: bool = True
    clipboard: bool = True
    manual: bool = True
    browser: bool = False
    ax_text: bool = False
    keyboard_chunk: bool = False
    ocr: bool = False


class IdleConfig(BaseModel):
    threshold_sec: int = 180


class ClipboardConfig(BaseModel):
    max_text_length: int = 2000
    dedup_window_sec: int = 600


class AXTextConfig(BaseModel):
    poll_interval_sec: float = 1.0


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


class KeyboardChunkConfig(BaseModel):
    silence_sec: float = 2.0
    force_flush_sec: float = 2.0
    store_text: bool = True


class OCRConfig(BaseModel):
    provider: Literal["vision_native"] = "vision_native"
    window_switch_delay_sec: float = 0.8
    stable_interval_sec: float = 10.0
    keyboard_quiet_sec: float = 2.0


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
            "com.tinyspk.slack",
            "com.hnc.Discord",
            "com.tencent.wxwork",
            "com.alibaba.dingtalk",
            "com.bytedance.feishu",
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


class PipelineSignalsConfig(BaseModel):
    fs_enabled: bool = False
    fs_watch_paths: list[str] = Field(
        default_factory=lambda: ["~/Documents", "~/Desktop", "~/Downloads", "~/Go"]
    )
    fs_exclude: list[str] = Field(
        default_factory=lambda: ["node_modules", ".git", "__pycache__", ".venv", "dist", "build"]
    )
    browser_enabled: bool = True


class PipelineConfig(BaseModel):
    llm_mode: str = "local-first"
    max_llm_calls_per_run: int = 0
    max_llm_input_chars_per_run: int = 0
    feedback_path: str = "~/.keypulse/feedback.jsonl"
    signals: PipelineSignalsConfig = Field(default_factory=PipelineSignalsConfig)
    use_narrative_v2: bool = False
    use_narrative_skeleton: bool = False


class ModelBackendConfig(BaseModel):
    kind: Literal["lm_studio", "openai_compatible", "ollama", "disabled"] = "disabled"
    base_url: str = ""
    model: str = ""
    api_key_source: str = ""
    api_key_env: str = ""
    timeout_sec: int = 20


class ModelConfig(BaseModel):
    active_profile: Literal[
        "local-first",
        "cloud-first",
        "cloud-only",
        "local-only",
        "auto",
        "privacy-locked",
    ] = "local-first"
    state_path: str = "~/.keypulse/model-state.json"
    local: ModelBackendConfig = Field(
        default_factory=lambda: ModelBackendConfig(
            kind="lm_studio",
            base_url="http://127.0.0.1:1234",
            model="keypulse-local",
        )
    )
    cloud: ModelBackendConfig = Field(
        default_factory=lambda: ModelBackendConfig(
            kind="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="keypulse-cloud",
            api_key_env="OPENAI_API_KEY",
        )
    )


class ObsidianConfig(BaseModel):
    vault_path: str = "~/Go/Knowledge"
    vault_name: str = "KeyPulse"
    export_hour: int = 9
    export_minute: int = 5


class IntegrationConfig(BaseModel):
    standalone_output_path: str = "~/Go/Knowledge"
    state_path: str = "~/.keypulse/sink-state.json"


class PolicyConfig(BaseModel):
    scope_type: str
    scope_value: str
    mode: str  # allow|deny|metadata-only|redact|truncate
    enabled: bool = True
    priority: int = 100


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    watchers: WatchersConfig = Field(default_factory=WatchersConfig)
    idle: IdleConfig = Field(default_factory=IdleConfig)
    clipboard: ClipboardConfig = Field(default_factory=ClipboardConfig)
    ax_text: AXTextConfig = Field(default_factory=AXTextConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    keyboard_chunk: KeyboardChunkConfig = Field(default_factory=KeyboardChunkConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    policies: list[PolicyConfig] = Field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        """Load config from ~/.keypulse/config.toml or ./config.toml, falling back to defaults."""
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
