from __future__ import annotations

from datetime import datetime, timezone

from keypulse.pipeline.entity_extractor import Entity
from keypulse.pipeline.thing_clusterer import Thing
from keypulse.pipeline.thing_renderer import render_thing
from keypulse.sources.types import SemanticEvent


class _Gateway:
    def __init__(self, response: str = "ok", should_raise: bool = False):
        self.response = response
        self.should_raise = should_raise
        self.prompts: list[str] = []

    def render(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.should_raise:
            raise RuntimeError("boom")
        return self.response


def _thing() -> Thing:
    event = SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        source="git_log",
        actor="Harland",
        intent="修复 timeline",
        artifact="commit:11a3a9b",
        raw_ref="",
        privacy_tier="green",
        metadata={},
    )
    return Thing(
        id="t1",
        title="修了 timeline 段 (commit:11a3a9b)",
        entities=[Entity(kind="commit", value="11a3a9b", raw="11a3a9b", confidence=1.0)],
        events=[event],
        time_start=event.time,
        time_end=event.time,
        sources={"git_log"},
    )


def test_render_thing_uses_gateway_output() -> None:
    gateway = _Gateway(response="### 标题\n- 问题：a\n- 做法：b\n- 结论：c\n- 产物：d")
    output = render_thing(_thing(), model_gateway=gateway)
    assert "### 标题" in output
    assert gateway.prompts
    assert "事件流（按时间排序）" in gateway.prompts[0]


def test_render_thing_fallback_when_gateway_none() -> None:
    output = render_thing(_thing(), model_gateway=None)
    assert "### 修了 timeline 段" in output
    assert "- 问题：-" in output
    assert "- 做法：涉及 1 个事件" in output


def test_render_thing_fallback_when_gateway_raises() -> None:
    gateway = _Gateway(should_raise=True)
    output = render_thing(_thing(), model_gateway=gateway)
    assert "- 结论：-" in output
    assert "- 产物：11a3a9b" in output


def test_render_thing_fallback_handles_empty_entities() -> None:
    t = _thing()
    t.entities = []
    output = render_thing(t, model_gateway=None)
    assert "- 产物：-" in output
