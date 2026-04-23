import re
from keypulse.privacy.detectors import detect, has_sensitive_content


def desensitize(text: str, redact_emails: bool = True, redact_phones: bool = True, redact_tokens: bool = True) -> str:
    """Replace sensitive patterns with [PATTERN_NAME] placeholders."""
    if not text:
        return text
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


def truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"
