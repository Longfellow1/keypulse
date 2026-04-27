from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from keypulse.obsidian.exporter import export_obsidian


DATE = "2026-04-22"


def _event(
    time: str,
    title: str,
    *,
    body: str | None = None,
    tags: str = "keypulse,sync",
    topic_title: str | None = None,
) -> dict[str, str]:
    body_text = body or title
    return {
        "source": "manual",
        "event_type": "manual_save",
        "ts_start": f"{DATE}T{time}+00:00",
        "ts_end": f"{DATE}T{time}+00:00",
        "created_at": f"{DATE}T{time}+00:00",
        "app_name": "Terminal",
        "window_title": title,
        "title": topic_title or title,
        "body": body_text,
        "content_text": body_text,
        "speaker": "user",
        "metadata_json": json.dumps({"tags": tags}, ensure_ascii=False),
        "tags": tags,
    }


def _install_fake_data(monkeypatch, dataset: list[dict[str, str]], sessions: list[dict[str, str]] | None = None):
    sessions = sessions or []

    def fake_query_raw_events(source=None, app_name=None, since=None, until=None, limit=500):
        rows = list(dataset)
        if source:
            rows = [row for row in rows if row["source"] == source]
        if app_name:
            rows = [row for row in rows if app_name in row.get("app_name", "")]
        if since:
            rows = [row for row in rows if row["ts_start"] >= since]
        if until:
            rows = [row for row in rows if row["ts_start"] <= until]
        rows.sort(key=lambda row: row["ts_start"], reverse=True)
        return rows[:limit]

    def fake_get_sessions(date_str=None, limit=500):
        if date_str:
            return list(sessions)
        return list(sessions)

    monkeypatch.setattr("keypulse.obsidian.exporter.query_raw_events", fake_query_raw_events)
    monkeypatch.setattr("keypulse.store.repository.get_sessions", fake_get_sessions)


def _patch_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _extract_section(text: str, heading: str) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text)
    return match.group(0) if match else ""


def _daily_note(vault: Path) -> Path:
    return vault / "Daily" / f"{DATE}.md"


def _dashboard_note(vault: Path) -> Path:
    return vault / "Dashboard" / "Today.md"


def _event_links(text: str) -> list[str]:
    return re.findall(r"\[\[Events/[^\]]+\]\]", text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _get_topic_evidence_section(topic_path: Path) -> str:
    return _extract_section(_read(topic_path), "## 相关证据")


def test_full_sync_creates_complete_files(monkeypatch, tmp_path: Path):
    _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "修复 keypulse 安装问题", tags="keypulse,install"),
        _event("09:15:00", "整理 launchd plist", tags="launchd,plist"),
    ]
    _install_fake_data(monkeypatch, dataset)

    written = export_obsidian(vault, date_str=DATE)

    assert written
    assert _daily_note(vault).exists()
    assert not _dashboard_note(vault).exists()
    assert any((vault / "Events" / DATE).glob("*.md"))
    daily = _read(_daily_note(vault))
    assert "## 今天的事件卡" in daily
    assert "## 今天涉及的主题" in daily


def test_incremental_appends_three_new_events_without_rewriting_narrative(monkeypatch, tmp_path: Path):
    _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "修复 keypulse 安装问题", tags="keypulse,install"),
        _event("09:15:00", "整理 launchd plist", tags="launchd,plist"),
        _event("09:30:00", "回顾同步流程", tags="obsidian,sync"),
        _event("10:00:00", "梳理 launchd 计划", tags="launchd,plist"),
        _event("10:15:00", "补写增量游标", tags="obsidian,sync"),
    ]
    _install_fake_data(monkeypatch, dataset)

    export_obsidian(vault, date_str=DATE)
    before = _read(_daily_note(vault))
    narrative_before = _extract_section(before, "## 今日主线")

    dataset.extend(
        [
            _event("11:00:00", "补充 hourly plist", tags="launchd,plist"),
            _event("11:15:00", "确认增量 sync 行为", tags="obsidian,sync"),
            _event("11:30:00", "补充测试说明", tags="obsidian,sync"),
        ]
    )
    export_obsidian(vault, date_str=DATE, incremental=True)

    after = _read(_daily_note(vault))
    assert _extract_section(after, "## 今日主线") == narrative_before
    assert len(_event_links(after)) == len(_event_links(before)) + 3
    assert "## 今天的事件卡" in after


def test_incremental_preserves_daily_protected_sections(monkeypatch, tmp_path: Path):
    _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "修复 keypulse 安装问题", tags="keypulse,install"),
        _event("09:20:00", "整理 launchd plist", tags="launchd,plist"),
        _event("09:40:00", "回顾同步流程", tags="obsidian,sync"),
    ]
    _install_fake_data(monkeypatch, dataset)

    export_obsidian(vault, date_str=DATE)
    before = _read(_daily_note(vault))
    main_before = _extract_section(before, "## 今日主线")
    decide_before = _extract_section(before, "## 需要你决定")
    tomorrow_before = _extract_section(before, "## 明天的锚点")

    dataset.append(_event("11:00:00", "补充 hourly plist", tags="launchd,plist"))
    export_obsidian(vault, date_str=DATE, incremental=True)

    after = _read(_daily_note(vault))
    assert _extract_section(after, "## 今日主线") == main_before
    assert _extract_section(after, "## 需要你决定") == decide_before
    assert _extract_section(after, "## 明天的锚点") == tomorrow_before


def test_incremental_is_idempotent_for_same_batch(monkeypatch, tmp_path: Path):
    _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "修复 keypulse 安装问题", tags="keypulse,install"),
        _event("09:20:00", "整理 launchd plist", tags="launchd,plist"),
        _event("09:40:00", "回顾同步流程", tags="obsidian,sync"),
    ]
    _install_fake_data(monkeypatch, dataset)

    export_obsidian(vault, date_str=DATE)

    batch = [
        _event("11:00:00", "补充 hourly plist", tags="launchd,plist"),
        _event("11:15:00", "确认增量 sync 行为", tags="obsidian,sync"),
        _event("11:30:00", "补充测试说明", tags="obsidian,sync"),
    ]
    dataset.extend(batch)
    export_obsidian(vault, date_str=DATE, incremental=True)
    after_first_incremental = _read(_daily_note(vault))

    export_obsidian(vault, date_str=DATE, incremental=True)
    after_second_incremental = _read(_daily_note(vault))

    assert after_second_incremental == after_first_incremental


@pytest.mark.parametrize("cursor_state", ["missing", "corrupt"])
def test_incremental_falls_back_when_cursor_is_missing_or_corrupt(monkeypatch, tmp_path: Path, cursor_state: str):
    home = _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "修复 keypulse 安装问题", tags="keypulse,install"),
        _event("09:20:00", "整理 launchd plist", tags="launchd,plist"),
        _event("09:40:00", "回顾同步流程", tags="obsidian,sync"),
    ]
    _install_fake_data(monkeypatch, dataset)

    export_obsidian(vault, date_str=DATE)

    dataset.extend(
        [
            _event("11:00:00", "补充 hourly plist", tags="launchd,plist"),
            _event("11:15:00", "确认增量 sync 行为", tags="obsidian,sync"),
        ]
    )
    export_obsidian(vault, date_str=DATE, incremental=True)
    before = _read(_daily_note(vault))

    cursor_path = home / ".keypulse" / "sync-cursor.json"
    if cursor_state == "missing":
        cursor_path.unlink()
    else:
        cursor_path.write_text("{not-json", encoding="utf-8")

    export_obsidian(vault, date_str=DATE, incremental=True)
    after = _read(_daily_note(vault))

    assert after == before


def test_incremental_appends_topic_evidence_without_duplicates(monkeypatch, tmp_path: Path):
    _patch_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault"
    dataset = [
        _event("09:00:00", "project alpha 里整理 launchd", tags="project,alpha"),
        _event("09:20:00", "project alpha 里补笔记", tags="project,alpha"),
    ]
    _install_fake_data(monkeypatch, dataset)

    export_obsidian(vault, date_str=DATE)

    event_dir = vault / "Events" / DATE
    first_event = sorted(event_dir.glob("*.md"))[0]
    first_event_target = first_event.relative_to(vault).with_suffix("").as_posix()
    topic_path = vault / "Topics" / "project-alpha.md"
    _write(
        topic_path,
        "\n".join(
            [
                "---",
                "type: topic",
                "source: keypulse",
                f"date: {DATE}",
                "vault: KeyPulse",
                "---",
                "",
                "# project alpha",
                "",
                "- 知识库：KeyPulse",
                "- 关联片段：1",
                "",
                "## 相关证据",
                f"- [[{first_event_target}|project alpha 里整理 launchd]] - project alpha 里整理 launchd",
                "",
            ]
        ),
    )

    dataset.append(_event("11:00:00", "project alpha 里补增量证据", tags="project,alpha"))
    export_obsidian(vault, date_str=DATE, incremental=True)

    evidence_after = _get_topic_evidence_section(topic_path)
    assert evidence_after.count("[[Events/") == 2
    assert "project alpha 里补增量证据" in evidence_after
