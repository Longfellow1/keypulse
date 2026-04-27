from __future__ import annotations

import re
from typing import Any

from keypulse.privacy.detectors import detect, has_sensitive_content


_SHELL_LINE_PATTERNS = (
    re.compile(r"(?m)^\s*sudo\s+\S.*$"),
    re.compile(r"(?m)^\s*export\s+[A-Z_][A-Z0-9_]*=.*$"),
    re.compile(r"(?m)^\s*(brew|npm|pip|pipx|uv|cargo|gem|apt|yum|dnf|port)\s+(install|tap|update|upgrade|remove|uninstall)\b.*$"),
    re.compile(r"(?m)^\s*cat\s*>>?\s*~?/[\w./-]+\s*<<['\"]?\w+['\"]?$"),
    re.compile(r"(?m)^\s*\S+\s*>>?\s*~?/[\w./-]+$"),
    re.compile(r"(?m)^Last login:\s+\w+\s+\w+\s+\d+.*$"),
    re.compile(r"(?m)^==>\s+(Installing|Pouring|Caveats|Downloading)\b.*$"),
    re.compile(r"(?m)^\s*(which|type)\s+\S+$"),
)


def _redact_shell_lines(text: str) -> str:
    redacted = text
    for pattern in _SHELL_LINE_PATTERNS:
        redacted = pattern.sub("[REDACTED_SHELL]", redacted)
    return redacted


def desensitize(text: str, redact_emails: bool = True, redact_phones: bool = True, redact_tokens: bool = True) -> str:
    """Replace sensitive patterns with [PATTERN_NAME] placeholders."""
    if not text:
        return text
    text = _redact_shell_lines(text)
    enabled = {
        "email": redact_emails,
        "phone_cn": redact_phones,
        "id_card": True,
        "bank_card": True,
        "api_key": redact_tokens,
        "bearer_token": redact_tokens,
        "jwt": redact_tokens,
        "aws_key": redact_tokens,
        "url_token": redact_tokens,
        "private_key": redact_tokens,
        "uuid_v4": redact_tokens,
        "openai_key": redact_tokens,
        "github_pat": redact_tokens,
        "slack_token": redact_tokens,
    }
    detections = detect(text, enabled)
    if not detections:
        return text
    result = []
    prev = 0
    for d in detections:
        result.append(text[prev:d.start])
        result.append("[REDACTED]")
        prev = d.end
    result.append(text[prev:])
    return "".join(result)


def desensitize_json_value(
    value: Any,
    *,
    redact_emails: bool = True,
    redact_phones: bool = True,
    redact_tokens: bool = True,
) -> Any:
    if isinstance(value, str):
        return desensitize(
            value,
            redact_emails=redact_emails,
            redact_phones=redact_phones,
            redact_tokens=redact_tokens,
        )
    if isinstance(value, dict):
        return {
            key: desensitize_json_value(
                item,
                redact_emails=redact_emails,
                redact_phones=redact_phones,
                redact_tokens=redact_tokens,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            desensitize_json_value(
                item,
                redact_emails=redact_emails,
                redact_phones=redact_phones,
                redact_tokens=redact_tokens,
            )
            for item in value
        ]
    return value


def truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"
