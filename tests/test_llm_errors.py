from __future__ import annotations

from keypulse.pipeline.llm_errors import LLMErrorKind, classify_llm_error


def test_classify_llm_error_timeout() -> None:
    kind = classify_llm_error(TimeoutError("timed out while calling backend"))
    assert kind == LLMErrorKind.timeout


def test_classify_llm_error_rate_limit() -> None:
    kind = classify_llm_error(RuntimeError("HTTP Error 429: Too Many Requests"))
    assert kind == LLMErrorKind.rate_limit


def test_classify_llm_error_unknown() -> None:
    kind = classify_llm_error(RuntimeError("unexpected boom"))
    assert kind == LLMErrorKind.unknown
