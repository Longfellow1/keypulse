from __future__ import annotations

import re

from datetime import datetime, timezone

from keypulse.pipeline.things import build_things, render_things_report
from tests.fixtures.things_pipeline import StubGateway, make_fixture_events


SINCE = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 4, 29, 0, 0, tzinfo=timezone.utc)


def _run_pipeline(monkeypatch):
    events = make_fixture_events()
    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(events))
    gateway = StubGateway()
    things = build_things(SINCE, UNTIL, model_gateway=gateway)
    report = render_things_report(things, model_gateway=gateway, title="今日做的事")
    return things, report, gateway


def test_pipeline_happy_path(monkeypatch) -> None:
    things, report, gateway = _run_pipeline(monkeypatch)
    print(f"things_count={len(things)}")
    assert things
    assert len(things) >= 2
    assert report.strip()
    assert gateway.calls


def test_no_fallback_strings(monkeypatch) -> None:
    _, report, _ = _run_pipeline(monkeypatch)
    assert "_其他" not in report
    assert "未知来源" not in report
    assert "全部" not in report


def test_thing_titles_valid(monkeypatch) -> None:
    _, report, _ = _run_pipeline(monkeypatch)
    lines = report.splitlines()
    title_indexes = [idx for idx, line in enumerate(lines) if line.startswith("### ")]
    assert title_indexes

    for idx in title_indexes:
        title = lines[idx][4:].strip()
        assert title
        assert len(title) <= 20

        next_non_empty = ""
        for follow in lines[idx + 1 :]:
            if follow.strip():
                next_non_empty = follow.strip()
                break
        assert next_non_empty
        assert not next_non_empty.startswith("### ")


def test_overview_section_present(monkeypatch) -> None:
    _, report, _ = _run_pipeline(monkeypatch)
    match = re.search(r"## 今日概览\n\n([\s\S]*?)(\n## |\n### |\Z)", report)
    assert match is not None
    overview_block = match.group(1).strip()
    overview_len = len(overview_block)
    assert 80 <= overview_len <= 180


def test_key_facts_preserved(monkeypatch) -> None:
    _, report, _ = _run_pipeline(monkeypatch)
    assert "11a3a9b" in report
    assert "wx.mail.qq.com" in report
    assert "Codex CLI" in report
    assert "KeyPulse" in report


def test_pipeline_llm_failure_uses_fallback(monkeypatch) -> None:
    events = make_fixture_events()
    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(events))
    things = build_things(SINCE, UNTIL, model_gateway=StubGateway(should_raise=True))
    assert things
    assert any("生成失败，仅显示骨架" in thing.narrative for thing in things)
