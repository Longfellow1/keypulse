from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.pipeline.outline_prompt import (
    ThingOutline,
    build_outline_prompt,
    parse_outline_response,
    request_outline,
)
from keypulse.pipeline.session_splitter import ActivitySession
from keypulse.sources.types import SemanticEvent


class _Gateway:
    def __init__(self, response: str = "", should_raise: bool = False):
        self.response = response
        self.should_raise = should_raise
        self.prompts: list[str] = []

    def render(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.should_raise:
            raise RuntimeError("boom")
        return self.response


def _event(index: int) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=index),
        source="git_log",
        actor="Harland",
        intent=f"实现 timeline 功能 {index}" + " x" * 30,
        artifact=f"keypulse/pipeline/file_{index}.py",
        raw_ref="",
        privacy_tier="green",
        metadata={},
    )


def _session(count: int = 3) -> ActivitySession:
    events = [_event(i) for i in range(count)]
    return ActivitySession(
        id="session-1",
        time_start=events[0].time,
        time_end=events[-1].time,
        events=events,
    )


def test_build_outline_prompt_contains_window_and_truncation() -> None:
    session = _session(52)

    prompt = build_outline_prompt(session)

    assert "时间窗：2026-04-28T10:00:00+00:00 到 2026-04-28T10:51:00+00:00" in prompt
    assert "事件数：52" in prompt
    assert "... 还有 2 条" in prompt


def test_parse_outline_response_tolerates_numbering_and_pipe() -> None:
    response = """1. 修复 timeline 段隐私回归 | 补丁覆盖 git 与对话证据\n2) 设计 session 拆分\n\n3. 回归验证 | 跑 pytest 与真链路"""

    outlines = parse_outline_response(response)

    assert outlines == [
        ThingOutline(title="修复 timeline 段隐私回归", summary_hint="补丁覆盖 git 与对话证据"),
        ThingOutline(title="设计 session 拆分", summary_hint=""),
        ThingOutline(title="回归验证", summary_hint="跑 pytest 与真链路"),
    ]


def test_parse_outline_response_fallback_on_empty() -> None:
    outlines = parse_outline_response("\n\n")
    assert outlines == [ThingOutline(title="活动 session", summary_hint="")]


def test_request_outline_uses_gateway_and_parses() -> None:
    gateway = _Gateway(response="1. 修复 timeline | 合并跨源事件")
    outlines = request_outline(_session(), model_gateway=gateway)

    assert outlines == [ThingOutline(title="修复 timeline", summary_hint="合并跨源事件")]
    assert gateway.prompts and "事件摘要" in gateway.prompts[0]


def test_request_outline_fallback_when_gateway_none_or_error() -> None:
    session = _session(4)

    none_outlines = request_outline(session, model_gateway=None)
    error_outlines = request_outline(session, model_gateway=_Gateway(should_raise=True))

    assert none_outlines == [ThingOutline(title="活动 session 1", summary_hint="4 个事件")]
    assert error_outlines == [ThingOutline(title="活动 session 1", summary_hint="4 个事件")]
