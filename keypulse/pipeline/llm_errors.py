from __future__ import annotations

import re
from enum import Enum
from typing import Iterable
from urllib.error import HTTPError, URLError


class LLMErrorKind(str, Enum):
    timeout = "timeout"
    rate_limit = "rate_limit"
    auth = "auth"
    payload_too_big = "payload_too_big"
    server = "server"
    network = "network"
    unknown = "unknown"


_HTTP_CODE_RE = re.compile(r"\b([45]\d{2})\b")


def _walk_exc_chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None:
        key = id(current)
        if key in seen:
            break
        seen.add(key)
        yield current
        current = current.__cause__ or current.__context__


def _contains_timeout(text: str) -> bool:
    return "timeout" in text or "timed out" in text


def classify_llm_error(exc: BaseException) -> LLMErrorKind:
    for item in _walk_exc_chain(exc):
        if isinstance(item, TimeoutError):
            return LLMErrorKind.timeout
        if isinstance(item, URLError):
            reason = str(getattr(item, "reason", "") or "").lower()
            if _contains_timeout(reason):
                return LLMErrorKind.timeout
            return LLMErrorKind.network
        if isinstance(item, HTTPError):
            if item.code in (401, 403):
                return LLMErrorKind.auth
            if item.code == 408:
                return LLMErrorKind.timeout
            if item.code == 413:
                return LLMErrorKind.payload_too_big
            if item.code == 429:
                return LLMErrorKind.rate_limit
            if 500 <= item.code < 600:
                return LLMErrorKind.server

        text = str(item or "").lower()
        if _contains_timeout(text):
            return LLMErrorKind.timeout
        if "too many requests" in text or "rate limit" in text or "rate_limit" in text or "http error 429" in text:
            return LLMErrorKind.rate_limit
        if "unauthorized" in text or "forbidden" in text or "invalid api key" in text or "auth" in text:
            if "oauth" not in text:
                return LLMErrorKind.auth
        if "payload too large" in text or "request entity too large" in text or "http error 413" in text:
            return LLMErrorKind.payload_too_big
        if "name or service not known" in text or "temporary failure in name resolution" in text:
            return LLMErrorKind.network
        if "connection reset" in text or "connection refused" in text or "network is unreachable" in text:
            return LLMErrorKind.network

        code_match = _HTTP_CODE_RE.search(text)
        if code_match:
            code = int(code_match.group(1))
            if code in (401, 403):
                return LLMErrorKind.auth
            if code == 408:
                return LLMErrorKind.timeout
            if code == 413:
                return LLMErrorKind.payload_too_big
            if code == 429:
                return LLMErrorKind.rate_limit
            if 500 <= code < 600:
                return LLMErrorKind.server
            if code >= 500:
                return LLMErrorKind.server
        if "http error 5" in text or "server error" in text:
            return LLMErrorKind.server

    return LLMErrorKind.unknown
