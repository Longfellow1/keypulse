from __future__ import annotations

import re
from typing import Any, Mapping

from keypulse.config import Config

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKENISH_RE = re.compile(r"[a-zA-Z0-9_./:-]+")
_TOOL_ECHO_SOURCES = frozenset({"idle", "knowledgec", "zsh_history"})
_CONTEXT_SOURCES = frozenset({"window", "ax_text"})
_USER_MESSAGE_SOURCES = frozenset(
    {"clipboard", "manual", "markdown_vault", "claude_code", "codex_cli", "keyboard_chunk", "browser_url"}
)


def _estimate_token_count(text: str) -> int:
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    tokenish_count = len(_TOKENISH_RE.findall(text))
    return cjk_count + tokenish_count


def _source_kind(event: Mapping[str, Any]) -> str:
    speaker = str(event.get("speaker") or "").strip().lower()
    source = str(event.get("source") or "").strip().lower()
    if speaker in {"ai", "assistant"}:
        return "assistant_msg"
    if source in _CONTEXT_SOURCES:
        return "context"
    if speaker == "user" or source in _USER_MESSAGE_SOURCES:
        return "user_msg"
    if source in _TOOL_ECHO_SOURCES or speaker == "system":
        return "tool_echo"
    return "default"


def _event_value_density(event: Mapping[str, Any], settings: Any | None = None) -> float:
    cfg = settings or Config().pipeline.value_density
    if not bool(getattr(cfg, "enabled", True)):
        return 0.0

    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"))
        if str(part or "").strip()
    )
    token_target = max(int(getattr(cfg, "token_target", 80) or 80), 1)
    base = min(_estimate_token_count(text) / token_target, 1.0)
    weights = getattr(cfg, "source_weights", {}) or {}
    kind = _source_kind(event)
    fallback_weight = 0.85 if kind == "context" else weights.get("default", 0.6)
    weight = float(weights.get(kind, fallback_weight) or 0.0)
    score = base * max(weight, 0.0)

    decision_regex = str(getattr(cfg, "decision_regex", "") or "").strip()
    if decision_regex:
        try:
            if re.search(decision_regex, text):
                score += max(float(getattr(cfg, "decision_bonus", 0.0) or 0.0), 0.0)
        except re.error:
            pass

    return round(min(max(score, 0.0), 1.0), 4)


def _flagship_event_score(event: Mapping[str, Any]) -> float:
    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"), event.get("app_name"))
        if str(part or "").strip()
    )
    score = _event_value_density(event)
    if _CJK_RE.search(text):
        score += 0.3
    if 12 <= len(str(event.get("content_text") or "").strip()) <= 260:
        score += 0.15
    if _source_kind(event) == "context":
        win = str(event.get("window_title") or "")
        if win and (" - " in win or " — " in win or " – " in win):
            score += 0.4
    return round(score, 4)
