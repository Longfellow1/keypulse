from __future__ import annotations

from keypulse.config import Config
from keypulse.hud.state import add_attention_item, set_today_focus
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
    assert snapshot.top_signals
    assert snapshot.top_signals[0]["title"] == "今天重点关注产品决策和模型路由"


def test_build_hud_snapshot_reports_active_sources(tmp_path):
    db_path = tmp_path / "hud.db"
    cfg = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "log_path": str(tmp_path / "hud.log")},
            "watchers": {"ax_text": True, "keyboard_chunk": True, "ocr": False},
        }
    )
    init_db(cfg.db_path_expanded)

    snapshot = build_hud_snapshot(cfg, date_str="2026-04-19", hud_state_path=tmp_path / "hud-state.json")

    assert snapshot.active_sources["当前看到的正文"] is True
    assert snapshot.active_sources["键入整理片段"] is True
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
