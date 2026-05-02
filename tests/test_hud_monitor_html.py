from __future__ import annotations

from keypulse.hud.monitor_html import build_monitor_html
from keypulse.hud.summary import HUDSnapshot


def _snapshot() -> HUDSnapshot:
    return HUDSnapshot(
        date="2026-05-02",
        mode="standard",
        mode_label="标准模式",
        today_focus="重做 HUD",
        attention_items=[],
        summary_line="今天重点围绕 HUD",
        active_sources={"当前看到的正文": True},
        effective_count=48,
        filtered_count=13,
        theme_count=0,
        manual_marked_count=1,
        effective_count_delta_vs_yesterday=-410,
        filtered_count_delta_vs_yesterday=-27,
        theme_count_delta_vs_yesterday=0,
        manual_marked_count_delta_vs_yesterday=1,
        last_sync_at="2026-05-02T10:30:00+08:00",
        source_counts={},
        top_signals=[
            {
                "title": "C_Agents 当前状态 + 下一步 Brief",
                "source": "手动保存",
                "source_key": "manual",
                "reason": "明确表达、新信息、可复用",
                "score": 1.2,
                "path": "Daily/2026-05-02.md",
            },
            {
                "title": "菜单栏 HUD 要不要独立成软件？",
                "source": "窗口活动",
                "source_key": "window",
                "reason": "信息密度高",
                "score": 0.9,
                "path": "Daily/2026-05-02.md",
            },
        ],
    )


def test_monitor_html_matches_v1_hud_structure_without_mode_switcher():
    html = build_monitor_html(_snapshot(), capture_status="running", health_ok=True)

    assert '<div class="hud-after">' in html
    assert '<div class="hdr">' in html
    assert '<div class="stats">' in html
    assert '<div class="suggestions">' in html
    assert '<div class="actions">' in html
    assert '<div class="ftr">' in html
    assert "采集模式" not in html
    assert "seg-ctrl" not in html
    assert "mode-row" not in html


def test_monitor_html_uses_snapshot_values_and_delta_classes():
    html = build_monitor_html(_snapshot(), capture_status="running", health_ok=True)

    assert '<span class="hdr-name">KeyPulse</span>' in html
    assert '<span class="hdr-mode">标准</span>' in html
    assert '<div class="stat-num">48 <span class="delta down arr-down">410</span></div>' in html
    assert '<div class="stat-num">13 <span class="delta down arr-down">27</span></div>' in html
    assert '<div class="stat-num">0 <span class="delta flat">0</span></div>' in html
    assert '<div class="stat-num">1 <span class="delta arr-up">1</span></div>' in html
    assert "C_Agents 当前状态 + 下一步 Brief" in html
    assert '<span class="tag green">可复用</span>' in html
    assert '<span class="tag blue">新信息</span>' in html
    assert "今天第 2 天" in html
    assert "系统正常" in html


def test_monitor_html_emits_keypulse_action_links():
    html = build_monitor_html(_snapshot(), capture_status="paused", health_ok=False)

    assert 'href="keypulse://action/save-thought"' in html
    assert 'href="keypulse://action/set-intent"' in html
    assert 'href="keypulse://action/toggle-pause"' in html
    assert 'href="keypulse://action/quit"' in html
    assert "恢复" in html
    assert "系统需检查" in html
    assert '<div class="hdr-dot"></div>' in html


def test_monitor_html_escapes_dynamic_text():
    snapshot = _snapshot()
    escaped_snapshot = HUDSnapshot(
        **{
            **snapshot.__dict__,
            "top_signals": [
                {
                    "title": '<script>alert("x")</script>',
                    "source": "手动保存",
                    "source_key": "manual",
                    "reason": "明确表达",
                    "score": 1.0,
                    "path": "Daily/2026-05-02.md",
                }
            ],
        }
    )

    html = build_monitor_html(escaped_snapshot, capture_status="running", health_ok=True)

    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
