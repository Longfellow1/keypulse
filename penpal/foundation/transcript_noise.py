"""Pure rules for removing host-injected transcript wrapper text."""

from __future__ import annotations

import re

NOISE_PREFIXES: tuple[str, ...] = (
    "<environment_context", "<turn_aborted", "<subagent_notification", "<heartbeat",
    "<recommended_plugins", "<image name=", "</image", "<in-app-browser-context",
    "<codex_internal_context", "<user_instructions", "<permissions", "<collaboration_mode",
    "<user_action", "<system-reminder", "<command-name", "<command-message", "<command-args",
    "<local-command-", "Caveat:", "[Request interrupted", "<task-notification",
    "Stop hook feedback", "PreToolUse:", "PostToolUse:", "<bash-stdout", "<bash-stderr",
)

_USER_CONTENT_WRAPPERS = ("<pasted_content", "<bash-input")
_WRAPPER_RE = re.compile(r"^<([A-Za-z_][\w\-.]*)(?:\s[^>]*)?>.*</\1>$", re.DOTALL)
_INSTRUCTION_HEADER_RE = re.compile(
    r"^#\s*(AGENTS\.md|CLAUDE\.md)\b|^#\s*Instructions\s+from\b", re.IGNORECASE
)
_INLINE_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def is_noise_part(text: str) -> bool:
    """True when ``text`` is host-injected wrapper content rather than conversation."""
    head = text.lstrip()
    if not head:
        return True
    if head.startswith(NOISE_PREFIXES):
        return True
    if _INSTRUCTION_HEADER_RE.match(head):
        return True
    if "<INSTRUCTIONS>" in head[:400] and head.startswith("#"):
        return True
    if head.startswith(_USER_CONTENT_WRAPPERS):
        return False
    return bool(_WRAPPER_RE.match(head.rstrip()))


def clean_message_part(text: str) -> str:
    """Clean one message part, dropping wrapper-only text and cutting embedded reminders."""
    if "<system-reminder>" in text:
        text = _INLINE_REMINDER_RE.sub("", text)
    text = text.strip()
    return "" if is_noise_part(text) else text
