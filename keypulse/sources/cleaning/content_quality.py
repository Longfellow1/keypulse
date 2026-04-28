from __future__ import annotations

import re

from keypulse.sources.cleaning.config import load_cleaning_config
from keypulse.sources.types import SemanticEvent


_KNOWN_BLANK_URLS = {"about:blank", "chrome://newtab", "chrome://extensions"}
_NON_WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


def is_low_signal_event(event: SemanticEvent) -> tuple[bool, str]:
    intent = str(event.intent or "")
    normalized_intent = intent.strip().lower()
    artifact = str(event.artifact or "")
    normalized_artifact = _normalize_urlish(artifact)

    if event.source == "zsh_history":
        config = load_cleaning_config()
        if normalized_intent in {cmd.lower() for cmd in config.short_command_blacklist}:
            return True, "zsh_short_command_blacklist"
        if len(normalized_intent) <= 3:
            return True, "zsh_short_command_length"

    if event.source == "claude_code":
        compact = intent.strip()
        if compact.startswith("tool_use_id") or compact.startswith("tool_result") or compact.startswith("\n"):
            return True, "claude_system_message"

    if event.source in {"chrome_history", "safari_history"}:
        if not normalized_intent and (
            normalized_artifact in _KNOWN_BLANK_URLS
            or "oauth" in normalized_artifact
            or "callback" in normalized_artifact
        ):
            return True, "browser_blank_or_oauth"

    if "events/2026-" in artifact.lower() and "片段-" in artifact and artifact.endswith(".md"):
        return True, "markdown_vault_self_reference"
    raw_ref = str(event.raw_ref or "")
    if "events/2026-" in raw_ref.lower() and "片段-" in raw_ref and raw_ref.endswith(".md"):
        return True, "markdown_vault_self_reference"

    if len(normalized_intent) < 4 and _NON_WORD_RE.search(normalized_intent) is None:
        return True, "too_short_non_alnum"
    return False, ""


def _normalize_urlish(value: str) -> str:
    return value.split("?", 1)[0].split("#", 1)[0].strip().lower()
