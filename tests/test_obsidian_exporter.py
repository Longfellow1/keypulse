from __future__ import annotations

from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pytest

from keypulse.obsidian.exporter import (
    ExportWorkBlock,
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


def test_build_event_card_uses_fragment_filename_for_dirty_title(monkeypatch):
    monkeypatch.setattr("keypulse.obsidian.layout.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
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

    assert Path(card.path).name.startswith("1710-")
    assert "片段-" not in Path(card.path).name
    assert Path(card.path).suffix == ".md"
    assert "a-b" not in card.path
    assert len(Path(card.path).stem.split("-")) >= 2


def test_build_event_card_uses_slug_for_meaningful_title(monkeypatch):
    monkeypatch.setattr("keypulse.obsidian.layout.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
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

    assert Path(card.path).name.startswith("1710-")
    assert "片段-" not in Path(card.path).name
    assert re.search(r"-[0-9a-f]{8}\.md$", Path(card.path).name) is None


def test_build_event_card_humanize_titles_off_does_not_call_gateway(monkeypatch):
    class _Gateway:
        def call(self, *args, **kwargs):
            raise AssertionError("gateway.call should not be called when humanize_titles is off")

    monkeypatch.setattr("keypulse.obsidian.layout.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
    card = _build_event_card(
        _to_item(
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                ts_start="2026-04-20T09:10:00+00:00",
                ts_end="2026-04-20T09:16:00+00:00",
                title="make app bundle 实际成功了 preflight 没装",
                body="make app bundle 实际成功了 preflight 没装",
            )
        ),
        "2026-04-20",
        "build-app",
        model_gateway=_Gateway(),
        humanize_titles=False,
    )

    assert "build-app" in Path(card.path).stem


def test_build_event_card_humanize_titles_on_calls_gateway(monkeypatch):
    class _Gateway:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def call(self, capability, prompt, **kwargs):
            self.calls.append((capability, prompt))
            return {"filename_title": "build app bundle 成功 preflight 跳过"}

    gateway = _Gateway()
    monkeypatch.setattr("keypulse.obsidian.layout.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
    card = _build_event_card(
        _to_item(
            _make_item(
                created_at="2026-04-20T09:10:00+00:00",
                ts_start="2026-04-20T09:10:00+00:00",
                ts_end="2026-04-20T09:16:00+00:00",
                title="make app 实际成功了 bundle 已在 dist preflight 在 venv 没装",
                body="make app 实际成功了 bundle 已在 dist preflight 在 venv 没装",
            )
        ),
        "2026-04-20",
        "build-app",
        model_gateway=gateway,
        humanize_titles=True,
    )

    assert gateway.calls
    assert gateway.calls[0][0] == "event_title_humanize"
    assert "build-app-bundle-成功-preflight-跳过" in Path(card.path).name


def test_topic_title_handles_placeholder_keys():
    assert _topic_title("topic") == "未归类"
    assert _topic_title("uncategorized") == "未归类"
    assert _topic_title(None) == "未归类"


def test_build_obsidian_bundle_creates_daily_and_event_cards_for_single_item():
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    assert bundle["daily"][0]["path"] == "Daily/2026-04-18.md"
    assert set(bundle) == {"daily", "events", "topics"}
    assert bundle["daily"][0]["properties"]["type"] == "daily"
    assert bundle["events"][0]["properties"]["type"] == "event"
    assert "修复 keypulse 安装问题" in bundle["events"][0]["body"]
    assert "## 今天的事件卡" in bundle["daily"][0]["body"]
    assert "[[../.keypulse/events/" in bundle["daily"][0]["body"]
    assert bundle["topics"] == []


def test_build_obsidian_bundle_renders_relative_keypulse_links_by_default():
    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")
    daily_body = bundle["daily"][0]["body"]
    assert "[[../.keypulse/events/" in daily_body
    assert "file:///" not in daily_body


def test_build_obsidian_bundle_daily_contract_keeps_relative_event_links(monkeypatch, tmp_path: Path):
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
    assert "[[../.keypulse/events/" in daily_body
    assert "(file://" not in daily_body


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
    assert "## 今天的事件卡" not in bundle["daily"][0]["body"]
    assert bundle["topics"] == []


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


def test_write_obsidian_bundle_replaces_stale_event_files_for_same_day(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / ".keypulse"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    stale_dir = keypulse_home / "events" / "2026-04-18"
    stale_dir.mkdir(parents=True)
    stale_file = stale_dir / "0900-old-note.md"
    stale_file.write_text("old")

    bundle = build_obsidian_bundle([_sample_item()], vault_name="Harland Knowledge", date_str="2026-04-18")

    written = write_obsidian_bundle(bundle, tmp_path)

    assert written
    assert not stale_file.exists()
    assert any(path.parent == stale_dir for path in written)


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
