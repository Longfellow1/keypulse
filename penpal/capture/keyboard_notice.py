"""Keyboard text capture: the user-facing risk notice and the on/off state (issue #29).

Leaf module: it depends only on capture_control and the store state table, so the CLI can show the
notice and state without importing the capture watchers.

Keyboard capture is on by default (owner decision 2026-10-14, brain keyboard-capture-p0-experimental).
The off switch is the per-source switch in capture_control (`penpal source disable keyboard_chunk`).
"""
from __future__ import annotations

from penpal.capture.capture_control import disabled_sources
from penpal.i18n import T
from penpal.store.repository import get_state, set_state

KEYBOARD_SOURCE = "keyboard_chunk"
NOTICE_SHOWN_KEY = "keyboard_notice_shown"
DISABLE_COMMAND = f"penpal source disable {KEYBOARD_SOURCE}"
ENABLE_COMMAND = f"penpal source enable {KEYBOARD_SOURCE}"


def notice_text() -> str:
    """The one-time notice. Each line states only what the code does (filter in capture/hygiene.py,
    buckets in store/buckets.py, default grant in mcp/auth.py)."""
    return T(
        "\n".join([
            "键盘文本片段：默认开启。",
            "- 只保存经过过滤的文本片段，不逐键保存。",
            "- 过滤是尽力而为的，仍可能记录到敏感内容。",
            "- 片段按所在 App 归入分桶（如编辑器、终端归 Work）；AI 助手只能读取已授权的分桶，新授权默认只含 Work。",
            f"- 关闭：{DISABLE_COMMAND}",
        ]),
        "\n".join([
            "Keyboard text snippets: on by default.",
            "- Only filtered text snippets are stored, never individual keystrokes.",
            "- Filtering is best effort and can still capture sensitive text.",
            "- Snippets are tagged by app (e.g. editors and terminals are Work); AI hosts only read the buckets "
            "they were granted, and a new grant covers Work only.",
            f"- To turn off: {DISABLE_COMMAND}",
        ]),
    )


def keyboard_capture_enabled(watcher_enabled: bool) -> bool:
    """Effective state: the watcher is configured on and the source switch is not off."""
    return bool(watcher_enabled) and KEYBOARD_SOURCE not in disabled_sources()


def switch_hint(enabled: bool) -> str:
    if enabled:
        return T(f"关闭：{DISABLE_COMMAND}", f"To turn off: {DISABLE_COMMAND}")
    return T(f"重新开启：{ENABLE_COMMAND}", f"To turn on again: {ENABLE_COMMAND}")


def notice_pending() -> bool:
    """True until the notice has been shown once. An unreadable state counts as pending (show it)."""
    try:
        return not get_state(NOTICE_SHOWN_KEY)
    except Exception:
        return True


def mark_notice_shown() -> None:
    try:
        set_state(NOTICE_SHOWN_KEY, "1")
    except Exception:
        return  # the notice may repeat next time; nothing else depends on the flag
