"""Shared noise + self-echo helpers for the AI-transcript source plugins (claude_code, codex_cli).

Two jobs, one module so both plugins behave the same (D27):

* ``clean_message_part`` drops host-injected wrapper text that is not something the user or the
  assistant said (system reminders, slash-command wrappers, local command output, interrupt
  markers, Codex environment/instruction blocks).
* ``advance_echo`` / ``is_keypulse_call`` implement the ``via_keypulse`` mark: an assistant text
  that follows a call to a KeyPulse MCP tool (same session, until the next real user message) is
  mostly a paraphrase of KeyPulse's own output, so it is stored but flagged and hidden from search
  by default.
"""
from __future__ import annotations

import re
from typing import Iterable

# Prefixes of host-injected wrapper text (after lstrip). Shared by every transcript plugin.
NOISE_PREFIXES: tuple[str, ...] = (
    # Codex
    "<environment_context",
    "<turn_aborted",
    "<subagent_notification",
    "<heartbeat",
    "<recommended_plugins",
    "<image name=",
    "</image",
    "<in-app-browser-context",
    "<codex_internal_context",
    "<user_instructions",
    "<permissions",
    "<collaboration_mode",
    "<user_action",
    # Claude Code
    "<system-reminder",
    "<command-name",
    "<command-message",
    "<command-args",
    "<local-command-",  # -stdout / -stderr / -caveat
    "Caveat:",
    "[Request interrupted",
)

_WRAPPER_RE = re.compile(r"^<([A-Za-z_][\w\-.]*)(?:\s[^>]*)?>.*</\1>$", re.DOTALL)
_INSTRUCTION_HEADER_RE = re.compile(r"^#\s*(AGENTS\.md|CLAUDE\.md)\b|^#\s*Instructions\s+from\b", re.IGNORECASE)
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
    return bool(_WRAPPER_RE.match(head.rstrip()))


def clean_message_part(text: str) -> str:
    """Cleaned text of one message part, or "" when the whole part is noise. System reminders glued
    inside an otherwise real message are cut out."""
    if "<system-reminder>" in text:
        text = _INLINE_REMINDER_RE.sub("", text)
    text = text.strip()
    return "" if is_noise_part(text) else text


# ── via_keypulse (self-echo) ─────────────────────────────────────────────────────────────────

DEFAULT_SERVER_NAMES: tuple[str, ...] = ("keypulse",)


def _norm_names(names: Iterable[str] | None) -> tuple[str, ...]:
    out = tuple(n.strip().lower() for n in (names or ()) if isinstance(n, str) and n.strip())
    return out or DEFAULT_SERVER_NAMES


def resolve_server_names(override: Iterable[str] | None = None) -> tuple[str, ...]:
    """MCP server names KeyPulse is registered under: explicit override, else ``cleaning.toml``
    ``keypulse_server_names``, else ``("keypulse",)``."""
    if override is not None:
        return _norm_names(override)
    from keypulse.sources.cleaning.config import load_cleaning_config

    return _norm_names(load_cleaning_config().keypulse_server_names)


def _name_matches(value: object, names: tuple[str, ...]) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    return any(v.startswith((f"mcp__{n}__", f"{n}__", f"{n}.", f"{n}/")) for n in names)


def is_keypulse_call(payload: dict, names: tuple[str, ...]) -> bool:
    """Whether a tool-call record targets a KeyPulse MCP server.

    Covers Claude Code ``tool_use`` (``name: mcp__keypulse__search_context``) and the Codex
    shapes: ``function_call`` / ``custom_tool_call`` (``name`` = ``mcp__keypulse__x``, or
    ``namespace`` = ``mcp__keypulse__`` / ``keypulse`` with a bare ``name``) and
    ``mcp_tool_call*`` (``server`` or ``invocation.server`` = ``keypulse``).
    """
    if not isinstance(payload, dict):
        return False
    for key in ("name", "namespace", "tool"):
        if _name_matches(payload.get(key), names):
            return True
    containers = [payload]
    for key in ("invocation", "item"):
        if isinstance(payload.get(key), dict):
            containers.append(payload[key])
    for c in containers:
        server = c.get("server") or c.get("server_name") or c.get("namespace")
        if isinstance(server, str):
            s = server.strip().lower()
            if s in names or _name_matches(s, names) or any(s.startswith(f"mcp__{n}") for n in names):
                return True
    return False


def advance_echo(active: bool, role: str, has_text: bool, calls_keypulse: bool) -> tuple[bool, bool]:
    """One step of the per-session echo state machine. Returns ``(mark_this_message, new_active)``.

    A message is judged by the state *before* it (text that precedes a tool call in the same
    record is not an echo of it). A user message with real text ends the window.
    """
    marked = active and role == "assistant" and has_text
    if role == "user" and has_text:
        active = False
    if calls_keypulse:
        active = True
    return marked, active
