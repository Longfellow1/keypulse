from __future__ import annotations

from keypulse.hud.monitor_html import build_monitor_html
from keypulse.hud.summary import HUDSnapshot


def _snapshot(**overrides) -> HUDSnapshot:
    base = dict(
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
        service_status="ok",
        status_label="正常",
        hint_message="",
        hint_action="",
        companion_days=5,
    )
    base.update(overrides)
    return HUDSnapshot(**base)


def test_monitor_html_v1_1_structure():
    html = build_monitor_html(_snapshot(), capture_status="running", health_ok=True)

    # Plan A 主要骨架
    assert '<div class="hud">' in html
    assert '<div class="hdr">' in html
    assert '<div class="today-card">' in html
    assert 'id="todayFocus"' in html
    assert "今日重要的事儿" in html
    assert '<div class="stats">' in html
    assert '<div class="suggestions">' in html
    assert '<div class="ftr">' in html

    # 已被移除的旧元素
    assert "采集模式" not in html
    assert "seg-ctrl" not in html
    assert "mode-row" not in html
    assert "hdr-mode" not in html  # 标准 badge 已去除
    assert 'class="actions"' not in html  # 旧的 保存想法/设意图 按钮区已去除


def test_monitor_html_uses_snapshot_values_and_delta_classes():
    html = build_monitor_html(_snapshot(), capture_status="running", health_ok=True)

    assert '<span class="brand">KeyPulse</span>' in html
    assert '<span class="dot"></span>' in html
    assert '<span class="status-pill">正常</span>' in html
    assert '<div class="stat-num">48 <span class="delta down arr-down">410</span></div>' in html
    assert '<div class="stat-num">13 <span class="delta down arr-down">27</span></div>' in html
    assert '<div class="stat-num">0 <span class="delta flat">0</span></div>' in html
    assert '<div class="stat-num">1 <span class="delta arr-up">1</span></div>' in html
    assert "C_Agents 当前状态 + 下一步 Brief" in html
    assert '<span class="tag green">可复用</span>' in html
    assert '<span class="tag blue">新信息</span>' in html
    # 陪伴 N 天文案
    assert "和你一起记录的第 5 天" in html
    # today_focus 显示在 hero 卡里
    assert "重做 HUD" in html


def test_monitor_html_emits_v1_1_action_links():
    html = build_monitor_html(_snapshot(), capture_status="running", health_ok=True)

    # today_focus 走 inline input + JS save，URL 在 JS 里拼，HTML 不再有 edit-today-focus 链接
    assert "keypulse://action/save-today-focus?v=" in html
    assert 'href="keypulse://action/toggle-pause"' in html
    assert 'href="keypulse://action/restart-daemon"' in html
    assert 'href="keypulse://action/quit"' in html
    assert "⏸ 暂停" in html


def test_monitor_html_pause_state_shows_resume_and_gray_dot():
    snap = _snapshot(service_status="gray", status_label="已暂停")
    html = build_monitor_html(snap, capture_status="paused", health_ok=True)

    assert "▶ 恢复" in html
    assert '<span class="dot gray"></span>' in html
    assert '<span class="status-pill gray">已暂停</span>' in html


def test_monitor_html_warn_state_shows_hint_bar():
    snap = _snapshot(
        service_status="warn",
        status_label="LLM 异常",
        hint_message="LLM API key 失效或余额不足，请检查",
    )
    html = build_monitor_html(snap, capture_status="running", health_ok=True)

    assert '<span class="dot warn"></span>' in html
    assert '<span class="status-pill warn">LLM 异常</span>' in html
    assert 'class="hint-bar"' in html
    assert "LLM API key 失效或余额不足，请检查" in html
    # 没有 hint_action 时不渲染按钮（CSS 类定义存在，但不应出现按钮元素）
    assert 'class="hint-btn"' not in html
    assert "打开设置" not in html


def test_monitor_html_hint_bar_renders_action_button():
    snap = _snapshot(
        service_status="err",
        status_label="采集异常",
        hint_message="辅助功能权限未授权",
        hint_action="open://x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    )
    html = build_monitor_html(snap, capture_status="running", health_ok=False)

    assert "打开设置" in html
    assert "keypulse://action/open-url?u=" in html
    # 编码后的目标 URL 必须含原协议
    assert "x-apple.systempreferences" in html


def test_monitor_html_err_state_shows_red_dot():
    snap = _snapshot(
        service_status="err",
        status_label="采集异常",
        hint_message="输入监控权限不足，请前往系统设置 → 隐私 → 输入监控",
    )
    html = build_monitor_html(snap, capture_status="running", health_ok=False)

    assert '<span class="dot err"></span>' in html
    assert '<span class="status-pill err">采集异常</span>' in html
    assert "输入监控权限不足" in html


def test_monitor_html_empty_today_focus_shows_placeholder():
    snap = _snapshot(today_focus="")
    html = build_monitor_html(snap, capture_status="running", health_ok=True)

    assert "今天最想完成的一件事" in html
    assert 'today-input filled' not in html
    # 空状态时 input 不应带 value 属性
    assert ' value=""' not in html


def test_monitor_html_escapes_dynamic_text():
    snap = _snapshot(
        top_signals=[
            {
                "title": '<script>alert("x")</script>',
                "source": "手动保存",
                "source_key": "manual",
                "reason": "明确表达",
                "score": 1.0,
                "path": "Daily/2026-05-02.md",
            }
        ]
    )

    html = build_monitor_html(snap, capture_status="running", health_ok=True)

    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
