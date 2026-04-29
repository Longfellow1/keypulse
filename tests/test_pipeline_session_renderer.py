from __future__ import annotations

from datetime import datetime, timezone

import pytest

from keypulse.pipeline.model import PipelineQualityError
from keypulse.pipeline.session_renderer import render_session_things
from keypulse.pipeline.session_splitter import ActivitySession
from keypulse.sources.types import SemanticEvent


def _event(ts: str, source: str, intent: str) -> SemanticEvent:
    return SemanticEvent(
        time=datetime.fromisoformat(ts),
        source=source,
        actor="Harland",
        intent=intent,
        artifact="",
        raw_ref="",
        privacy_tier="green",
        metadata={},
    )


def _session() -> ActivitySession:
    events = [
        _event("2026-04-28T01:00:00+00:00", "git_log", "KeyPulse commit 11a3a9b 落地"),
        _event("2026-04-28T01:05:00+00:00", "terminal", "在 Codex CLI 跑 pytest 验收"),
    ]
    return ActivitySession(
        id="session-1",
        time_start=events[0].time,
        time_end=events[-1].time,
        events=events,
    )


class _Gateway:
    def __init__(self, response: str = "", *, raise_exc: bool = False):
        self.response = response
        self.raise_exc = raise_exc
        self.prompts: list[str] = []

    def render(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.raise_exc:
            raise RuntimeError("boom")
        return self.response


def test_renders_multiple_things_from_session() -> None:
    gateway = _Gateway(
        response=(
            "### KeyPulse 落地\n"
            "凌晨你在 Git 里把 commit 11a3a9b 推上去。\n\n"
            "### 验收测试\n"
            "在 Terminal 用 Codex CLI 跑 pytest 确认通过。"
        )
    )
    things = render_session_things(_session(), model_gateway=gateway)
    assert len(things) == 2
    assert things[0].title == "KeyPulse 落地"
    assert "11a3a9b" in things[0].narrative
    assert things[1].title == "验收测试"
    assert "Codex CLI" in things[1].narrative


def test_raises_on_gateway_none() -> None:
    with pytest.raises(PipelineQualityError):
        render_session_things(_session(), model_gateway=None)


def test_raises_on_gateway_failure() -> None:
    gateway = _Gateway(raise_exc=True)
    with pytest.raises(PipelineQualityError):
        render_session_things(_session(), model_gateway=gateway)


def test_raises_on_empty_response() -> None:
    gateway = _Gateway(response="")
    with pytest.raises(PipelineQualityError):
        render_session_things(_session(), model_gateway=gateway)


def test_raises_on_parse_zero_things() -> None:
    gateway = _Gateway(response="一段没有 markdown 标题的纯文本")
    with pytest.raises(PipelineQualityError):
        render_session_things(_session(), model_gateway=gateway)


def test_thing_carries_session_metadata() -> None:
    gateway = _Gateway(response="### 一件事\n一段简单叙事")
    things = render_session_things(_session(), model_gateway=gateway)
    assert len(things) == 1
    thing = things[0]
    assert thing.events == _session().events
    assert thing.sources == {"git_log", "terminal"}
    assert thing.time_start == _session().time_start
    assert thing.time_end == _session().time_end
    assert thing.narrative == "一段简单叙事"
