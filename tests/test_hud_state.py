from __future__ import annotations

from keypulse.hud.state import add_attention_item, read_hud_state, remove_attention_item, set_hud_mode, set_today_focus


def test_hud_state_persists_focus_mode_and_attention(tmp_path):
    state_path = tmp_path / "hud-state.json"

    set_hud_mode("focus", state_path)
    set_today_focus("今天重点看产品决策", date_str="2026-04-19", path=state_path)
    add_attention_item("融资", state_path)
    add_attention_item("模型路由", state_path)

    state = read_hud_state(state_path)

    assert state.mode == "focus"
    assert state.today_focus["2026-04-19"] == "今天重点看产品决策"
    assert state.attention_items == ["融资", "模型路由"]


def test_hud_state_can_remove_attention_and_clear_focus(tmp_path):
    state_path = tmp_path / "hud-state.json"

    add_attention_item("融资", state_path)
    add_attention_item("模型路由", state_path)
    set_today_focus("今天只看成本", date_str="2026-04-19", path=state_path)

    remove_attention_item("融资", state_path)
    set_today_focus("", date_str="2026-04-19", path=state_path)

    state = read_hud_state(state_path)

    assert state.attention_items == ["模型路由"]
    assert "2026-04-19" not in state.today_focus
