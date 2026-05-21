from __future__ import annotations

from pathlib import Path

import pytest

import keypulse.i18n as i18n
from keypulse.obsidian.exporter import (
    ExportWorkBlock,
    _weekday_label,
    _is_meaningful_topic,
    _render_dashboard_blocks,
    _to_item,
    _topic_from_item,
    _topic_title,
    build_obsidian_bundle,
    write_obsidian_bundle,
)
from keypulse.obsidian.layout import slugify

WorkBlock = ExportWorkBlock


def _sample_item():
    return {
        "created_at": "2026-04-18T09:00:00+00:00",
        "source": "manual",
        "event_type": "manual_save",
        "title": "修复 keypulse 安装问题",
        "body": "pyobjc dependency and PIP_USER conflict",
        "app_name": "Terminal",
        "tags": "keypulse,install",
    }


def _make_item(**overrides):
    item = {
        "created_at": "2026-04-20T09:10:00+00:00",
        "source": "manual",
        "event_type": "manual_save",
        "title": "修复 keypulse 安装问题",
        "body": "pyobjc dependency and PIP_USER conflict",
        "app_name": "Terminal",
        "window_title": "Terminal",
        "tags": "keypulse,install",
        "session_id": "session-1",
        "ts_start": "2026-04-20T09:10:00+00:00",
        "ts_end": "2026-04-20T09:16:00+00:00",
    }
    item.update(overrides)
    return item


def test_slugify_preserves_chinese_characters():
    assert slugify("中文 Topic") == "中文-topic"


def test_weekday_label_is_i18n_aware(monkeypatch):
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    assert _weekday_label("2026-05-21") == "周四"

    monkeypatch.setenv("KEYPULSE_LANG", "en")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    assert _weekday_label("2026-05-21") == "Thu"


@pytest.mark.parametrize(
    "value",
    [
        "12345",
        "2026-04-20",
        "123-456",
        "http://example.com",
        "https://example.com/path",
        "https-example-com",
        "/Users/harland/notes/todo.md",
        "users-harland-notes-todo",
        "library-cache",
        "opt-homebrew-bin",
        "readme.md",
        "script.py",
        "archive.rar",
        "sheet.xlsx",
        "slides.pptx",
        "sk-1234567890abcdef",
        "sk_1234567890abcdef",
        "deadbeefdeadbeef",
        "foo --bar",
        "-1920-1080",
        "A B",
        "单",
        "",
    ],
)
def test_is_meaningful_topic_rejects_noise(value: str):
    assert not _is_meaningful_topic(value)


@pytest.mark.parametrize(
    "value",
    [
        "中文主题",
        "修复 keypulse 安装问题",
        "release planning notes",
        "分析 数据 导出",
    ],
)
def test_is_meaningful_topic_accepts_substantive_values(value: str):
    assert _is_meaningful_topic(value)


def test_topic_from_item_returns_none_for_noisy_title():
    assert _topic_from_item({"title": "https://example.com", "body": "https://example.com"}) is None


def test_topic_from_item_prefers_meaningful_title_when_tags_are_noisy():
    assert _topic_from_item({"tags": "https://example.com", "title": "修复 keypulse 安装问题"}) == "修复-keypulse-安装问题"


def test_topic_from_item_ignores_app_name_as_topic_source():
    assert _topic_from_item({"app_name": "终端", "body": "docker compose config"}) == "docker-compose-config"


def test_topic_from_item_returns_none_when_only_app_name_is_present():
    assert _topic_from_item({"app_name": "终端"}) is None


@pytest.mark.parametrize(
    "value",
    [None, "", "topic", "uncategorized"],
)
def test_topic_title_falls_back_to_uncategorized(value):
    assert _topic_title(value) == "未归类"


def test_build_obsidian_bundle_returns_daily_only_for_noisy_topic():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                title="https://example.com",
                body="https://example.com",
                tags="https://example.com",
            )
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
    )

    assert set(bundle) == {"daily"}
    daily_body = bundle["daily"][0]["body"]
    assert "Topics/" not in daily_body
    assert "Events/" not in daily_body


def test_build_obsidian_bundle_creates_daily_note_for_single_item():
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    assert bundle["daily"][0]["path"] == "Daily/2026-04-18.md"
    assert set(bundle) == {"daily"}
    assert bundle["daily"][0]["properties"]["type"] == "daily"
    assert "## 今天的事件卡" not in bundle["daily"][0]["body"]
    assert "[[../.keypulse/events/" not in bundle["daily"][0]["body"]


def test_build_obsidian_bundle_wiki_link_mode_does_not_emit_legacy_file_links(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    bundle = build_obsidian_bundle(
        [_sample_item()],
        vault_name="Harland Knowledge",
        date_str="2026-04-18",
        wiki_link_mode="absolute_md",
    )
    daily_body = bundle["daily"][0]["body"]
    assert "[[../.keypulse/events/" not in daily_body
    assert "(file://" not in daily_body


def test_build_obsidian_bundle_skips_loginwindow_items_from_counts():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                app_name="loginwindow",
                window_title="loginwindow",
                title="loginwindow",
                body="loginwindow",
                session_id="session-loginwindow",
            )
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-18",
    )

    assert bundle["daily"][0]["properties"]["item_count"] == 0
    assert "## 今天的事件卡" not in bundle["daily"][0]["body"]


def test_to_item_derives_manual_title_from_body_when_missing():
    item = _to_item(
        {
            "created_at": "2026-04-18T10:00:00+00:00",
            "source": "manual",
            "event_type": "manual_save",
            "title": "",
            "body": "今天把 retention 启动崩溃修掉，并补了回归测试",
            "app_name": "",
            "tags": "keypulse,retention",
            "content_text": "今天把 retention 启动崩溃修掉，并补了回归测试",
            "window_title": "",
            "speaker": "user",
        }
    )

    assert item is not None
    assert item["title"] == "今天把 retention 启动崩溃修掉，并补了回归测试"


def test_write_obsidian_bundle_writes_markdown_notes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEYPULSE_HOME", str(tmp_path / ".keypulse"))
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    written = write_obsidian_bundle(bundle, tmp_path)

    assert written
    daily = tmp_path / "Daily" / "2026-04-18.md"
    assert daily.exists()
    content = daily.read_text()
    assert content.startswith("---")
    assert "type: daily" in content
    assert "source: keypulse" in content


def test_write_obsidian_bundle_keeps_stale_event_files_for_same_day(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / ".keypulse"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    stale_dir = keypulse_home / "events" / "2026-04-18"
    stale_dir.mkdir(parents=True)
    stale_file = stale_dir / "0900-old-note.md"
    stale_file.write_text("old")

    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    written = write_obsidian_bundle(bundle, tmp_path)

    assert written
    assert stale_file.exists()
    assert all(path.parent != stale_dir for path in written)


def test_write_obsidian_bundle_keeps_historical_event_files_untouched(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / ".keypulse"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    historical_dir = keypulse_home / "events" / "2026-04-17"
    historical_dir.mkdir(parents=True)
    historical_file = historical_dir / "0915-old-history.md"
    historical_file.write_text("history")

    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")
    write_obsidian_bundle(bundle, tmp_path)

    assert historical_file.exists()


def test_write_obsidian_bundle_writes_placeholder_when_quality_gate_refuses(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEYPULSE_HOME", str(tmp_path / ".keypulse"))
    daily_dir = tmp_path / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = daily_dir / "2026-04-18.md"
    daily_path.write_text(
        "\n".join(
            [
                "# 2026-04-18",
                "",
                "### 事项1",
                "a b c d e f g h i j",
                "",
                "### 事项2",
                "a b c d e f g h i j",
                "",
                "### 事项3",
                "a b c d e f g h i j",
                "",
                "### 事项4",
                "a b c d e f g h i j",
            ]
        ),
        encoding="utf-8",
    )

    bundle = build_obsidian_bundle(
        [
            _make_item(
                app_name="loginwindow",
                window_title="loginwindow",
                title="loginwindow",
                body="loginwindow",
                session_id="session-loginwindow",
            )
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-18",
    )

    written = write_obsidian_bundle(bundle, tmp_path)

    assert daily_path in written
    content = daily_path.read_text(encoding="utf-8")
    assert "采集异常" in content
    assert "quality_gate REFUSED" in content


def test_render_dashboard_blocks_limits_to_top_five_non_fragments():
    blocks = [
        WorkBlock(
            theme=f"topic-{index}",
            duration_sec=600 - index * 10,
            ts_start=f"2026-04-18T0{index}:00:00+00:00",
            ts_end=f"2026-04-18T0{index}:10:00+00:00",
            primary_app="Codex",
            event_count=2,
            key_candidates=[],
            continuity="new",
        )
        for index in range(6)
    ]
    blocks.append(
        WorkBlock(
            theme="碎片",
            duration_sec=30,
            ts_start="2026-04-18T09:00:00+00:00",
            ts_end="2026-04-18T09:00:30+00:00",
            primary_app="Codex",
            event_count=1,
            key_candidates=[],
            continuity="new",
            fragment=True,
        )
    )

    body = _render_dashboard_blocks(blocks)

    assert body.count("### ") == 5
    assert "topic-0" in body
    assert "topic-5" not in body
