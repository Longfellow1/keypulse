from __future__ import annotations
import tomllib
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


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


class IdleConfig(BaseModel):
    threshold_sec: int = 180


class ClipboardConfig(BaseModel):
    max_text_length: int = 2000
    dedup_window_sec: int = 600


class PrivacyConfig(BaseModel):
    redact_emails: bool = True
    redact_phones: bool = True
    redact_tokens: bool = True


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
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
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
