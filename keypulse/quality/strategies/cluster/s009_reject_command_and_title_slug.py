"""S009 – Reject command invocations and app-title slugs surfaced as topics.

Patterns caught (all operate on the slugified, lowercase, hyphen-separated form):

1. Command prefix + ASCII-only tail: ^(git|cd|ls|...) - <ascii-only-tail>
2. Extra path prefixes / extensions not yet covered by S004: ^home-, -sh$, etc.
3. Multi-digit slug (≥3 numeric segments): window-title tokeniser artefact.
4. CJK-leading slug + known git/shell action suffix: 先删除现有的-origin
"""

from __future__ import annotations

import re

from keypulse.quality.base import Strategy, Verdict

# fmt: off
_CMD_PREFIX = re.compile(
    r"^(?:git|cd|ls|rm|cat|curl|cp|mv|mkdir|echo|brew|pip|npm|yarn|pnpm|"
    r"docker|kubectl|ssh|scp|touch|chmod|chown|sudo|gh|keypulse|tmutil|"
    r"defaults|launchctl|open|which|less|more|tail|head|grep|find|awk|sed|"
    r"pytest|make|go|cargo|rustc|node|tsc|uvicorn|uv|pip3)-"
)
# fmt: on

# S004 already blocks users- / .md / .py; extend here for other paths
_EXTRA_PATH_PREFIX = re.compile(r"^home-")
_EXTRA_PATH_SUFFIX = re.compile(r"-(?:txt|toml|json|yml|yaml|sh|log)$")

# 3+ hyphen-separated purely-numeric segments anywhere in the slug
_MULTI_DIGIT = re.compile(r"(?:(?:^|-)\d+){3}")

# CJK range for mixed-content detection
_HAS_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_ASCII_ONLY = re.compile(r"^[a-z0-9][-a-z0-9]*$")

# CJK-leading slug ending with a git/shell action word (e.g. 先删除现有的-origin)
_CJK_LEAD_CMD_SUFFIX = re.compile(
    r"^[\u4e00-\u9fff\u3040-\u30ff]"           # starts with CJK
    r".*-"                                       # any content + dash
    r"(?:origin|remote|push|pull|commit|merge|rebase|clone|fetch|"
    r"branch|checkout|stash|reset|revert|status|log|diff|add|rm|mv)$"
)


class S009RejectCommandAndTitleSlug(Strategy):
    id = "S009"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject command-invocation and app-title slugs"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()

        if _EXTRA_PATH_PREFIX.match(text):
            return Verdict(accept=False, reason="command-title-slug")

        if _EXTRA_PATH_SUFFIX.search(text):
            return Verdict(accept=False, reason="command-title-slug")

        if _MULTI_DIGIT.search(text):
            return Verdict(accept=False, reason="command-title-slug")

        if _CJK_LEAD_CMD_SUFFIX.match(text):
            return Verdict(accept=False, reason="command-title-slug")

        if _CMD_PREFIX.match(text):
            after_first_dash = text[text.index("-") + 1:]
            # Reject when tail is pure ASCII (e.g. git-push-u-origin-main)
            if _ASCII_ONLY.match(after_first_dash):
                return Verdict(accept=False, reason="command-title-slug")
            # Also reject when NO CJK at all (covers cd-go-corpusflow etc.)
            if not _HAS_CJK.search(after_first_dash):
                return Verdict(accept=False, reason="command-title-slug")
            # CJK present after command prefix → keep (keypulse-笔友)

        return Verdict(accept=True)
