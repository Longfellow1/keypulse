"""Shared value_density calculation module for daily orchestrator and narrative sync paths."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping

from keypulse.config import Config


_CJK_RE = re.compile(r"[一-鿿]")
_TOKENISH_RE = re.compile(r"[a-zA-Z0-9_./:-]+")
# === OCR watcher 已下线 2026-05-14 ===
# 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
# 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
# 历史 raw_events 中 ocr_text_capture 数据保留可读
_TOOL_ECHO_SOURCES = frozenset({"ax_text", "window", "idle", "knowledgec", "zsh_history"})
_USER_MESSAGE_SOURCES = frozenset({"clipboard", "manual", "markdown_vault", "claude_code", "codex_cli"})


def _source_kind(event: Mapping[str, Any]) -> str:
    """Classify event source type for density weighting."""
    speaker = str(event.get("speaker") or "").strip().lower()
    source = str(event.get("source") or "").strip().lower()
    if speaker in {"ai", "assistant"}:
        return "assistant_msg"
    if source in _TOOL_ECHO_SOURCES or speaker == "system":
        return "tool_echo"
    if speaker == "user" or source in _USER_MESSAGE_SOURCES:
        return "user_msg"
    return "default"


def _estimate_token_count(text: str) -> int:
    """Estimate token count by combining CJK character count and tokenish count."""
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    tokenish_count = len(_TOKENISH_RE.findall(text))
    return cjk_count + tokenish_count


@lru_cache(maxsize=1)
def _load_value_density_config_cached() -> Any:
    """Load value_density config from Config singleton with caching."""
    try:
        cfg = Config.load()
        return load_value_density_config(cfg)
    except Exception:
        return None


def load_value_density_config(cfg: Any) -> Any:
    """Load value_density config from a Config object."""
    if cfg is None:
        return None
    daily_cfg = getattr(cfg, "daily", None)
    if daily_cfg is not None:
        density_cfg = getattr(daily_cfg, "value_density", None)
        if density_cfg is not None:
            return density_cfg
    pipeline_cfg = getattr(cfg, "pipeline", None)
    if pipeline_cfg is None:
        return None
    return getattr(pipeline_cfg, "value_density", None)


def compute_value_density(event: Mapping[str, Any], config: Any | None = None) -> float:
    """Compute importance/value density score for an event.

    Args:
        event: Event dict with content_text, window_title, source, speaker
        config: value_density config object; if None, loads from Config singleton

    Returns:
        Float score [0.0, 1.0] combining token count, source weight, and decision bonus
    """
    if config is None:
        # Attempt to load from singleton, but fallback gracefully
        try:
            config = _load_value_density_config_cached()
        except Exception:
            config = None

    if not config:
        # Fallback: use sensible defaults when config unavailable
        config = _build_default_config()

    if not bool(getattr(config, "enabled", True)):
        return 0.0

    # Combine content text and window title
    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"))
        if str(part or "").strip()
    )

    # Base score from token count
    token_target = max(int(getattr(config, "token_target", 80) or 80), 1)
    base = min(_estimate_token_count(text) / token_target, 1.0)

    # Apply source weight
    weights = getattr(config, "source_weights", {}) or {}
    weight = float(weights.get(_source_kind(event), weights.get("default", 0.6)) or 0.0)
    score = base * max(weight, 0.0)

    # Apply decision bonus if decision regex matches
    decision_regex = str(getattr(config, "decision_regex", "") or "").strip()
    if decision_regex:
        try:
            if re.search(decision_regex, text):
                score += max(float(getattr(config, "decision_bonus", 0.0) or 0.0), 0.0)
        except re.error:
            # Silently ignore regex errors
            pass

    return round(min(max(score, 0.0), 1.0), 4)


def _build_default_config() -> Any:
    """Build a default config object when Config loading fails."""
    from types import SimpleNamespace

    return SimpleNamespace(
        enabled=True,
        token_target=80,
        decision_bonus=0.25,
        source_weights={
            "user_msg": 1.25,
            "assistant_msg": 0.85,
            "tool_echo": 0.25,
            "default": 0.6,
        },
        decision_regex="是不是|为什么|决定|选择?|选|根因|结论|判断|取舍|方案|建议|应该|必须|确认|拍板|原因",
    )
