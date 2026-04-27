from __future__ import annotations

from pathlib import Path

import pytest

from keypulse.obsidian.exporter import (
    _build_event_card,
    _is_meaningful_topic,
    _render_dashboard_blocks,
    _to_item,
    _topic_from_item,
    _topic_title,
    build_obsidian_bundle,
    write_obsidian_bundle,
)
from keypulse.obsidian.layout import slugify
from keypulse.pipeline.narrative import WorkBlock


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


def _topic_bundle_with_two_blocks(*, first_end: str, second_start: str, second_end: str, topic_title: str):
    return build_obsidian_bundle(
        [
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                ts_start="2026-04-20T09:10:00+00:00",
                ts_end=first_end,
                title=topic_title,
                body=topic_title,
                tags="alpha,beta,gamma",
            ),
            _make_item(
                created_at=second_start,
                ts_start=second_start,
                ts_end=second_end,
                title=topic_title,
                body=topic_title,
                tags="alpha,beta,gamma",
                session_id="session-2",
            ),
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
    )


def test_slugify_preserves_chinese_characters():
    assert slugify("中文 Topic") == "中文-topic"


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


def test_build_obsidian_bundle_routes_noisy_topics_to_uncategorized():
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

    assert bundle["topics"] == []
    assert bundle["events"][0]["properties"]["topic"] == "uncategorized"
    assert "未归类" in bundle["events"][0]["body"]


def test_build_obsidian_bundle_skips_topic_cards_when_only_one_non_fragment_work_block_exists():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-1",
            ),
            _make_item(
                created_at="2026-04-20T09:25:00+00:00",
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-2",
            ),
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
        sessions=[
            {"id": "session-1", "duration_sec": 120},
            {"id": "session-2", "duration_sec": 360},
        ],
    )

    assert bundle["topics"] == []
    assert "Topics/" not in bundle["daily"][0]["body"]


def test_build_obsidian_bundle_creates_topic_card_after_two_non_fragment_work_blocks():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-1",
            ),
            _make_item(
                created_at="2026-04-20T09:25:00+00:00",
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-2",
            ),
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
        sessions=[
            {"id": "session-1", "duration_sec": 360},
            {"id": "session-2", "duration_sec": 420},
        ],
    )

    assert len(bundle["topics"]) == 1
    assert bundle["topics"][0]["properties"]["topic"] == "分析-数据-导出"
    assert "未归类" not in bundle["topics"][0]["body"]


def test_build_obsidian_bundle_uses_uncategorized_bucket_for_none_topics():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                title="123-456",
                body="123-456",
                tags="123-456",
            )
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
    )

    assert bundle["events"][0]["properties"]["topic"] == "uncategorized"
    assert bundle["topics"] == []


def test_build_obsidian_bundle_keeps_chinese_topic_slugs():
    bundle = build_obsidian_bundle(
        [
            _make_item(
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-1",
            ),
            _make_item(
                created_at="2026-04-20T09:25:00+00:00",
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="https://example.com",
                session_id="session-2",
            ),
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
        sessions=[
            {"id": "session-1", "duration_sec": 360},
            {"id": "session-2", "duration_sec": 360},
        ],
    )

    assert bundle["topics"][0]["path"] == "Topics/分析-数据-导出.md"


def test_build_event_card_uses_fragment_filename_for_dirty_title():
    card = _build_event_card(
        _to_item(
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                ts_start="2026-04-20T09:10:00+00:00",
                ts_end="2026-04-20T09:11:00+00:00",
                title="A B",
                body="A B",
                tags="alpha,beta,gamma",
            )
        ),
        "2026-04-20",
        "uncategorized",
    )

    assert Path(card.path).name.startswith("片段-0910-")
    assert Path(card.path).suffix == ".md"
    assert "A-B" not in card.path


def test_build_event_card_uses_slug_for_meaningful_title():
    card = _build_event_card(
        _to_item(
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                ts_start="2026-04-20T09:10:00+00:00",
                ts_end="2026-04-20T09:16:00+00:00",
                title="修复 keypulse 安装问题",
                body="修复 keypulse 安装问题",
                tags="https://example.com",
            )
        ),
        "2026-04-20",
        "修复-keypulse-安装问题",
    )

    assert Path(card.path).name.startswith("0910-修复-keypulse-安装问题-")
    assert "片段-" not in Path(card.path).name


def test_topic_title_handles_placeholder_keys():
    assert _topic_title("topic") == "未归类"
    assert _topic_title("uncategorized") == "未归类"
    assert _topic_title(None) == "未归类"


def test_build_obsidian_bundle_creates_daily_and_event_cards_for_single_item():
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    assert bundle["daily"][0]["path"] == "Daily/2026-04-18.md"
    assert bundle["dashboard"][0]["path"] == "Dashboard/Today.md"
    assert bundle["daily"][0]["properties"]["type"] == "daily"
    assert bundle["events"][0]["properties"]["type"] == "event"
    assert bundle["dashboard"][0]["properties"]["type"] == "dashboard"
    assert "修复 keypulse 安装问题" in bundle["events"][0]["body"]
    assert "## 🎯 今日主线" in bundle["dashboard"][0]["body"]
    assert "## 💡 需要你决定" in bundle["dashboard"][0]["body"]
    assert "## 已自动过滤的内容" in bundle["dashboard"][0]["body"]
    assert "## 正在形成的主题" in bundle["dashboard"][0]["body"]
    assert "[[Dashboard/Today]]" in bundle["daily"][0]["body"]
    assert "[[Events/" in bundle["daily"][0]["body"]
    assert bundle["topics"] == []


def test_build_obsidian_bundle_skips_loginwindow_event_cards_and_counts():
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

    assert bundle["events"] == []
    assert bundle["daily"][0]["properties"]["item_count"] == 0
    assert "事件卡：0" in bundle["daily"][0]["body"]
    assert bundle["topics"] == []


def test_write_obsidian_bundle_writes_markdown_notes(tmp_path: Path):
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    written = write_obsidian_bundle(bundle, tmp_path)

    assert written
    daily = tmp_path / "Daily" / "2026-04-18.md"
    dashboard = tmp_path / "Dashboard" / "Today.md"
    assert daily.exists()
    assert dashboard.exists()
    content = daily.read_text()
    assert content.startswith("---")
    assert "type: daily" in content
    assert "source: keypulse" in content
    assert "[[Dashboard/Today]]" in content


def test_write_obsidian_bundle_replaces_stale_event_files_for_same_day(tmp_path: Path):
    stale_dir = tmp_path / "Events" / "2026-04-18"
    stale_dir.mkdir(parents=True)
    stale_file = stale_dir / "0900-old-note.md"
    stale_file.write_text("old")

    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    written = write_obsidian_bundle(bundle, tmp_path)

    assert written
    assert not stale_file.exists()
    assert any(path.parent == stale_dir for path in written)


def test_build_obsidian_bundle_derives_manual_title_from_body_when_missing():
    bundle = build_obsidian_bundle(
        [
            {
                "created_at": "2026-04-18T10:00:00+00:00",
                "source": "manual",
                "event_type": "manual_save",
                "title": "",
                "body": "今天把 retention 启动崩溃修掉，并补了回归测试",
                "app_name": "",
                "tags": "keypulse,retention",
            }
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-18",
    )

    event_note = bundle["events"][0]
    assert "manual_save" not in event_note["body"]
    assert "# 今天把 retention 启动崩溃修掉，并补了回归测试" in event_note["body"]


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


def test_build_obsidian_bundle_prefers_skeleton_when_enabled(monkeypatch):
    class _Backend:
        kind = "openai_compatible"
        base_url = "https://example.com"
        model = "gpt-test"

    class _Gateway:
        def select_backend(self, stage: str = "write"):
            return _Backend()

    monkeypatch.setattr(
        "keypulse.obsidian.exporter.build_daily_skeleton_report",
        lambda *args, **kwargs: "# 2026-04-20 骨架报告\n\n## 今日主线\n- skeleton",
    )

    bundle = build_obsidian_bundle(
        [
            _make_item(
                title="分析 数据 导出",
                body="分析 数据 导出",
                tags="alpha,beta,gamma",
            )
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
        model_gateway=_Gateway(),
        use_narrative_skeleton=True,
        db_path="/tmp/keypulse-test.db",
    )

    daily_body = bundle["daily"][0]["body"]
    assert "骨架报告" in daily_body
    assert "## 需要你决定" in daily_body
    assert "## 今天的事件卡" in daily_body
