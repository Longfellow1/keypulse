from __future__ import annotations

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from keypulse.config import Config
from keypulse.hud.state import add_attention_item, dismiss_weekly_echo_for_week, set_today_focus
from keypulse.hud.summary import _status_symbol, build_hud_snapshot
from keypulse.store.db import init_db
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.store.repository import insert_raw_event, set_state


def test_build_hud_snapshot_uses_today_focus_and_attention_items(tmp_path):
    db_path = tmp_path / "hud.db"
    state_path = tmp_path / "hud-state.json"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)

    insert_raw_event(
        normalize_manual_event(
            text="今天重点关注产品决策和模型路由",
            ts_start="2026-04-19T10:00:00+08:00",
        )
    )
    set_state("last_flush", "2026-04-19T10:30:00+08:00")
    set_today_focus("产品决策", date_str="2026-04-19", path=state_path)
    add_attention_item("模型路由", state_path)

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=state_path)

    assert snapshot.mode_label == "标准模式"
    assert snapshot.today_focus == "产品决策"
    assert snapshot.attention_items == ["模型路由"]
    assert "产品决策" in snapshot.summary_line
    # top_signals 在单事件 / 未聚类场景下可为空 —— bundle.events 为 0 时
    # HUD 走兜底句，不强制有内容。具体 signals 行为由 monitor_html 测试覆盖。
    assert isinstance(snapshot.top_signals, list)


def test_obsidian_open_url_uses_vault_root_basename():
    """obsidian://open URL 用 vault_root 路径的 basename 作为 vault 名，
    不读 cfg.vault_name（那个是 KeyPulse 自己的别名，不是 Obsidian 协议名）。
    """
    from keypulse.hud.summary import _obsidian_open_url

    url = _obsidian_open_url("/Users/foo/Go/Knowledge", "Events/2026-05-05/2309-test.md")
    assert url.startswith("obsidian://open?vault=Knowledge&")
    assert "file=Events/2026-05-05/2309-test" in url
    # .md 扩展名应被去掉
    assert ".md" not in url


def test_obsidian_open_url_handles_tilde_or_unexpanded_paths():
    from keypulse.hud.summary import _obsidian_open_url

    # 不展开 ~ 也至少不崩
    url = _obsidian_open_url("/some/Vault Name", "Daily/x.md")
    assert "vault=Vault%20Name" in url


def test_obsidian_open_url_with_heading_anchor():
    from keypulse.hud.summary import _obsidian_open_url

    url = _obsidian_open_url(
        "/v/Knowledge",
        "Daily/2026-05-05.md",
        heading="凌晨访问pairdrop网站 · 5m",
    )
    # # 号被 percent-encoded，heading 文本保留
    assert "Daily/2026-05-05%23" in url
    assert "%E5%87%8C%E6%99%A8" in url  # "凌晨..." 中文编码


def test_parse_daily_topics_extracts_h3_under_main_section():
    from keypulse.hud.summary import _parse_daily_topics

    body = """# 2026-05-05

## 今日主线
## 今天做的事

### [[pairdrop|凌晨访问pairdrop网站]]

正文...

### [[weekly-plan|写周报方案]]

正文...

### 碎片汇总

> 11 个零散片段

## 今天的事件卡
"""
    topics = _parse_daily_topics(body)

    assert len(topics) == 2  # 碎片汇总 被过滤
    assert topics[0][0] == "凌晨访问pairdrop网站"
    assert topics[0][1] == "[[pairdrop|凌晨访问pairdrop网站]]"
    assert topics[1][0] == "写周报方案"


def test_parse_daily_topics_ignores_legacy_overview_section():
    from keypulse.hud.summary import _parse_daily_topics

    body = """# 今日做的事

## 今日概览

你围绕KeyPulse开展工作...

### 修改KeyPulse HUD代码并提交

正文...

### 访问PairDrop文件传输网站

正文...

## 今天的事件卡

- xxx
"""
    topics = _parse_daily_topics(body)

    assert topics == []


def test_parse_daily_topics_returns_empty_when_no_main_section():
    from keypulse.hud.summary import _parse_daily_topics

    assert _parse_daily_topics("") == []
    assert _parse_daily_topics("# 标题\n\n## 别的段\n\n### foo\n") == []


def test_build_hud_snapshot_reports_active_sources(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "watchers": {"ax_text": True, "ocr": False},
        }
    )
    init_db(cfg.db_path_expanded)

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=tmp_path / "hud-state.json")

    assert snapshot.active_sources["当前看到的正文"] is True
    assert snapshot.active_sources["屏幕识别补充"] is False


def test_build_hud_snapshot_reports_yesterday_deltas(tmp_path):
    db_path = tmp_path / "hud.db"
    state_path = tmp_path / "hud-state.json"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)

    insert_raw_event(
        normalize_manual_event(
            text="昨天重点关注产品决策",
            tags="alpha",
            ts_start="2026-04-18T10:00:00+08:00",
        )
    )
    insert_raw_event(
        normalize_manual_event(
            text="今天重点关注产品决策",
            tags="alpha,beta",
            ts_start="2026-04-19T10:00:00+08:00",
        )
    )
    insert_raw_event(
        normalize_manual_event(
            text="今天重点关注模型路由",
            tags="alpha,beta",
            ts_start="2026-04-19T11:00:00+08:00",
        )
    )
    set_state("last_flush", "2026-04-19T11:30:00+08:00")

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=state_path)

    assert snapshot.effective_count_delta_vs_yesterday == 1
    assert snapshot.filtered_count_delta_vs_yesterday == 0
    assert snapshot.theme_count_delta_vs_yesterday == 1
    assert snapshot.manual_marked_count_delta_vs_yesterday == 1


def test_build_hud_snapshot_uses_none_deltas_when_yesterday_missing(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)

    insert_raw_event(
        normalize_manual_event(
            text="今天重点关注产品决策",
            tags="alpha",
            ts_start="2026-04-19T10:00:00+08:00",
        )
    )

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=tmp_path / "hud-state.json")

    assert snapshot.effective_count_delta_vs_yesterday is None
    assert snapshot.filtered_count_delta_vs_yesterday is None
    assert snapshot.theme_count_delta_vs_yesterday is None
    assert snapshot.manual_marked_count_delta_vs_yesterday is None


def test_status_symbol_mapping():
    assert _status_symbol("running") == "●"
    assert _status_symbol("paused") == "⏸"
    assert _status_symbol("permission_denied") == "⊘"


def test_build_hud_snapshot_includes_weekly_notice_when_week_matches(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)
    set_state("weekly_notice", '{"week":"2026-W16","message":"本周数据不足，周报跳过"}')

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=tmp_path / "hud-state.json")

    assert snapshot.weekly_notice == "本周数据不足，周报跳过"


def test_build_hud_snapshot_hides_weekly_notice_when_week_not_match(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)
    set_state("weekly_notice", '{"week":"2026-W99","message":"本周数据不足，周报跳过"}')

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=tmp_path / "hud-state.json")

    assert snapshot.weekly_notice == ""


def test_build_hud_snapshot_shows_weekly_echo_banner_after_friday_2340(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)

    now_local = datetime(2026, 5, 8, 23, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    snapshot = build_hud_snapshot(
        cfg,
        date_str="2026-05-08",
        hud_state_path=tmp_path / "hud-state.json",
        now_local=now_local,
    )

    assert snapshot.weekly_echo_text == "本周回声 →"
    assert snapshot.weekly_echo_week == "2026-W19"
    assert "file=Weekly/2026-W19" in snapshot.weekly_echo_url


def test_build_hud_snapshot_hides_weekly_echo_after_dismiss_and_resets_next_week(tmp_path):
    db_path = tmp_path / "hud.db"
    state_path = tmp_path / "hud-state.json"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "obsidian": {"vault_path": str(tmp_path), "vault_name": "KeyPulse"},
        }
    )
    init_db(cfg.db_path_expanded)
    dismiss_weekly_echo_for_week("2026-W19", state_path)

    hidden_snapshot = build_hud_snapshot(
        cfg,
        date_str="2026-05-10",
        hud_state_path=state_path,
        now_local=datetime(2026, 5, 10, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert hidden_snapshot.weekly_echo_text == ""
    assert hidden_snapshot.weekly_echo_url == ""

    next_week_snapshot = build_hud_snapshot(
        cfg,
        date_str="2026-05-15",
        hud_state_path=state_path,
        now_local=datetime(2026, 5, 15, 23, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert next_week_snapshot.weekly_echo_text == "本周回声 →"
    assert next_week_snapshot.weekly_echo_week == "2026-W20"
