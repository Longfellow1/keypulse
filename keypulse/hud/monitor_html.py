from __future__ import annotations

import json
from html import escape
from urllib.parse import quote

from keypulse.hud.summary import HUDSnapshot


def _action_link(label: str, action: str, class_name: str, *, title: str | None = None) -> str:
    title_attr = f' title="{escape(title)}"' if title else ""
    return f'<a class="{class_name}" href="keypulse://action/{quote(action)}"{title_attr}>{escape(label)}</a>'


def _delta_html(delta: int | None) -> str:
    if delta is None:
        return ""
    if delta > 0:
        return f' <span class="delta arr-up">{delta}</span>'
    if delta < 0:
        return f' <span class="delta down arr-down">{abs(delta)}</span>'
    return ' <span class="delta flat">0</span>'


def _stat_cell(label: str, value: int, delta: int | None) -> str:
    return f"""
        <div class="stat-cell">
          <div class="stat-num">{value}{_delta_html(delta)}</div>
          <div class="stat-lbl">{escape(label)}</div>
        </div>
    """


def _signal_icon(title: str) -> str:
    for char in title.strip():
        if char.isascii() and char.isalnum():
            return char.upper()
    return "#"


def _tag_class(label: str) -> str:
    if label == "可复用":
        return "tag green"
    if label == "新信息":
        return "tag blue"
    return "tag"


def _signal_tags(reason: str) -> list[str]:
    tags = [item.strip() for item in reason.replace(",", "、").split("、") if item.strip()]
    return tags[:3] or ["高价值"]


def _signal_item(signal: dict[str, object]) -> str:
    title = str(signal.get("title") or "未命名提示")
    reason = str(signal.get("reason") or "")
    obsidian_url = str(signal.get("obsidian_url") or "")
    tags = "".join(f'<span class="{_tag_class(tag)}">{escape(tag)}</span>' for tag in _signal_tags(reason))
    if obsidian_url:
        href = f"keypulse://action/open-url?u={quote(obsidian_url, safe='')}"
        return f"""
        <a class="sugg-item" href="{href}" title="在 Obsidian 中打开">
          <div class="sugg-icon">{escape(_signal_icon(title))}</div>
          <div class="sugg-text">
            <div class="sugg-title">{escape(title)}</div>
            <div class="sugg-tags">{tags}</div>
          </div>
          <div class="sugg-arrow">→</div>
        </a>
        """
    return f"""
        <div class="sugg-item">
          <div class="sugg-icon">{escape(_signal_icon(title))}</div>
          <div class="sugg-text">
            <div class="sugg-title">{escape(title)}</div>
            <div class="sugg-tags">{tags}</div>
          </div>
        </div>
    """


def _placeholder_signal_item() -> str:
    return """
        <div class="sugg-item placeholder">
          <div class="sugg-icon">·</div>
          <div class="sugg-text">
            <div class="sugg-title">—</div>
          </div>
        </div>
    """


def _empty_signal_block() -> str:
    return """
        <div class="sugg-empty">今天还没记下什么，晚点回来看看</div>
    """


def _render_signals(signals: list[dict[str, object]]) -> str:
    if not signals:
        return _empty_signal_block()
    rendered = [_signal_item(signal) for signal in signals[:3]]
    while len(rendered) < 3:
        rendered.append(_placeholder_signal_item())
    return "".join(rendered)


def _status_pill_class(level: str) -> str:
    if level == "warn":
        return "status-pill warn"
    if level == "err":
        return "status-pill err"
    if level == "gray":
        return "status-pill gray"
    return "status-pill"


def _dot_class(level: str) -> str:
    if level == "warn":
        return "dot warn"
    if level == "err":
        return "dot err"
    if level == "gray":
        return "dot gray"
    return "dot"


def _hint_bar(hint: str, action: str = "") -> str:
    if not hint:
        return ""
    button_html = ""
    if action:
        url = action[len("open://"):] if action.startswith("open://") else action
        href = f"keypulse://action/open-url?u={quote(url, safe='')}"
        button_html = (
            f'<a class="hint-btn" href="{href}" title="打开系统设置">打开设置</a>'
        )
    return (
        '<div class="hint-bar">'
        f'<span class="hint-text">{escape(hint)}</span>'
        f'{button_html}'
        '</div>'
    )


def _today_card(today_focus: str) -> str:
    """Inline input 直接前台输入；已写过的内容当主角显示，没有 label。"""
    value_attr = f' value="{escape(today_focus, quote=True)}"' if today_focus else ""
    filled_cls = " filled" if today_focus else ""
    return f"""
      <div class="today-card">
        <span class="today-mark">✦</span>
        <input
          id="todayFocus"
          class="today-input{filled_cls}"
          type="text"
          placeholder="今天最想完成的一件事，回车保存"
          autocomplete="off"
          spellcheck="false"
          {value_attr}
        />
      </div>
    """


def _weekly_echo_top_banner(snapshot: HUDSnapshot) -> str:
    text = (snapshot.weekly_echo_text or "").strip()
    target_url = (snapshot.weekly_echo_url or "").strip()
    week = (snapshot.weekly_echo_week or "").strip()
    if not text or not target_url or not week:
        return ""
    href = (
        "keypulse://action/open-weekly-echo"
        f"?u={quote(target_url, safe='')}"
        f"&week={quote(week, safe='')}"
    )
    return f'<a class="weekly-echo-banner" href="{href}" title="打开本周周报">{escape(text)}</a>'


def _weekly_notice_banner(snapshot: HUDSnapshot) -> str:
    notice = (snapshot.weekly_notice or "").strip()
    if not notice:
        return ""
    return f'<div class="weekly-notice">{escape(notice)}</div>'


def build_monitor_data(snapshot: HUDSnapshot, *, capture_status: str) -> dict[str, object]:
    is_running = capture_status != "paused"
    return {
        "dotClass": _dot_class(snapshot.service_status),
        "statusPillClass": _status_pill_class(snapshot.service_status),
        "statusLabel": str(snapshot.status_label or ""),
        "weeklyEchoHtml": _weekly_echo_top_banner(snapshot),
        "hintHtml": _hint_bar(snapshot.hint_message, snapshot.hint_action),
        "weeklyNoticeHtml": _weekly_notice_banner(snapshot),
        "todayFocus": str(snapshot.today_focus or ""),
        "todayInputClass": "today-input filled" if snapshot.today_focus else "today-input",
        "statsHtml": (
            _stat_cell("记下来", snapshot.effective_count, snapshot.effective_count_delta_vs_yesterday)
            + _stat_cell("丢掉了", snapshot.filtered_count, snapshot.filtered_count_delta_vs_yesterday)
        ),
        "suggestionsHtml": _render_signals(list(snapshot.top_signals)),
        "pauseLabel": "⏸ 暂停" if is_running else "▶ 恢复",
        "companionText": f"和你一起记录的第 {snapshot.companion_days} 天",
    }


def build_monitor_html(snapshot: HUDSnapshot, *, capture_status: str) -> str:
    data = build_monitor_data(snapshot, capture_status=capture_status)
    initial_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    dot_class = escape(str(data["dotClass"]))
    status_pill_class = escape(str(data["statusPillClass"]))
    status_label = escape(str(data["statusLabel"]))
    weekly_echo_html = str(data["weeklyEchoHtml"])
    hint_html = str(data["hintHtml"])
    weekly_notice_html = str(data["weeklyNoticeHtml"])
    today_focus = str(data["todayFocus"])
    today_input_class = escape(str(data["todayInputClass"]))
    stats_html = str(data["statsHtml"])
    suggestions_html = str(data["suggestionsHtml"])
    pause_label = escape(str(data["pauseLabel"]))
    companion_text = escape(str(data["companionText"]))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KeyPulse HUD</title>
  <style>
    :root {{
      --bg-card: #ffffff;
      --bg-soft: #f5f5f7;
      --bg-input: #fafafa;
      --text-primary: #1c1c1e;
      --text-secondary: #48484a;
      --text-tertiary: #8e8e93;
      --border: #e5e5e7;
      --border-strong: #d1d1d6;
      --green: #34c759;
      --yellow: #ff9f0a;
      --red: #ff3b30;
      --gray: #8e8e93;
      --blue: #0a84ff;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ width: 365px; background: transparent; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', sans-serif;
      -webkit-font-smoothing: antialiased;
      color: var(--text-primary);
    }}
    a {{ text-decoration: none; -webkit-user-drag: none; color: inherit; }}

    .hud {{
      width: 365px;
      background: var(--bg-card);
      border-radius: 14px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}

    /* Header */
    .hdr {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 18px 14px;
    }}
    .hdr-left {{ display: flex; align-items: center; gap: 9px; }}
    .hdr-right {{ display: flex; align-items: center; gap: 8px; }}
    .dot {{
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 0 3px rgba(52,199,89,0.15);
    }}
    .dot.warn {{ background: var(--yellow); box-shadow: 0 0 0 3px rgba(255,159,10,0.18); }}
    .dot.err  {{ background: var(--red);    box-shadow: 0 0 0 3px rgba(255,59,48,0.18); }}
    .dot.gray {{ background: var(--gray);   box-shadow: 0 0 0 3px rgba(142,142,147,0.15); }}
    .brand {{
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    .status-pill {{
      font-size: 11px;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 20px;
      border: 0.5px solid var(--border-strong);
      color: var(--text-secondary);
      background: var(--bg-soft);
    }}
    .status-pill.warn {{ color: #b05d00; border-color: #f0c98a; background: #fff5e6; }}
    .status-pill.err  {{ color: #b00020; border-color: #f0a8a8; background: #ffeaea; }}
    .status-pill.gray {{ color: var(--text-tertiary); }}
    .weekly-echo-banner {{
      font-size: 11.5px;
      font-weight: 600;
      color: #7a4500;
      padding: 3px 8px;
      border-radius: 999px;
      border: 0.5px solid #f0c98a;
      background: #fff8e8;
      white-space: nowrap;
    }}
    .weekly-echo-banner:hover {{ background: #ffefc9; }}
    .weekly-notice {{
      margin: 0 18px 12px;
      padding: 8px 11px;
      border-radius: 8px;
      border: 0.5px solid #f0c98a;
      background: #fff8e8;
      color: #7a4500;
      font-size: 11.5px;
      line-height: 1.45;
    }}

    /* Hint bar (异常时) */
    .hint-bar {{
      margin: 0 18px 12px;
      padding: 8px 11px;
      background: #fff5e6;
      border: 0.5px solid #f0c98a;
      border-radius: 8px;
      font-size: 11.5px;
      line-height: 1.5;
      color: #7a4500;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .hint-text {{ flex: 1; }}
    .hint-btn {{
      flex-shrink: 0;
      font-size: 11px;
      font-weight: 500;
      padding: 4px 9px;
      border-radius: 6px;
      border: 0.5px solid #d99a3e;
      background: #ffe4b8;
      color: #7a4500;
      cursor: pointer;
      white-space: nowrap;
      text-decoration: none;
    }}
    .hint-btn:hover {{ background: #ffd28a; }}

    /* Today card: inline input, 已写过的内容当主角，没有 label */
    .today-card {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 18px 14px;
      padding: 12px 14px;
      background: var(--bg-input);
      border: 0.5px solid var(--border);
      border-radius: 10px;
    }}
    .today-mark {{
      color: var(--blue);
      font-size: 14px;
      flex-shrink: 0;
    }}
    .today-input {{
      flex: 1;
      font-size: 14px;
      color: var(--text-primary);
      line-height: 1.45;
      width: 100%;
      border: none;
      outline: none;
      background: transparent;
      padding: 0;
      font-family: inherit;
      -webkit-appearance: none;
    }}
    .today-input::placeholder {{ color: var(--text-tertiary); font-weight: 400; }}
    .today-input.filled {{ font-weight: 500; }}
    .today-input.saved-flash {{ color: var(--green); transition: color 0.6s; }}

    /* Stats grid */
    .stats {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      border-top: 0.5px solid var(--border);
      border-bottom: 0.5px solid var(--border);
    }}
    .stat-cell {{
      padding: 13px 0 12px;
      text-align: center;
      border-right: 0.5px solid var(--border);
      min-width: 0;
    }}
    .stat-cell:last-child {{ border-right: none; }}
    .stat-num {{
      font-size: 20px;
      font-weight: 600;
      color: var(--text-primary);
      line-height: 1;
      letter-spacing: -0.02em;
      white-space: nowrap;
    }}
    .stat-num .delta {{ font-size: 11px; font-weight: 500; color: var(--green); margin-left: 2px; }}
    .stat-num .delta.down {{ color: var(--red); }}
    .stat-num .delta.flat {{ color: var(--text-tertiary); }}
    .stat-lbl {{ font-size: 11px; color: var(--text-tertiary); margin-top: 5px; font-weight: 500; }}

    /* Suggestions */
    .suggestions {{
      padding: 14px 18px 10px;
    }}
    .sugg-header {{
      font-size: 11px;
      font-weight: 600;
      color: var(--text-tertiary);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    .sugg-item {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 10px;
      margin: 0 -10px;
      border-radius: 8px;
      color: inherit;
    }}
    a.sugg-item {{ cursor: pointer; }}
    a.sugg-item:hover {{ background: var(--bg-soft); }}
    a.sugg-item:hover .sugg-arrow {{ color: var(--text-secondary); }}
    .sugg-item.placeholder {{ opacity: 0.35; pointer-events: none; }}
    .sugg-item.placeholder .sugg-icon {{ background: transparent; border-color: transparent; }}
    .sugg-arrow {{
      flex-shrink: 0;
      font-size: 12px;
      color: var(--text-tertiary);
      margin-left: 4px;
    }}
    .sugg-empty {{
      padding: 14px 10px 6px;
      margin: 0 -10px;
      font-size: 12.5px;
      color: var(--text-tertiary);
      line-height: 1.5;
    }}
    .sugg-icon {{
      width: 22px; height: 22px;
      border-radius: 6px;
      background: var(--bg-soft);
      border: 0.5px solid var(--border);
      flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-secondary);
    }}
    .sugg-text {{ flex: 1; min-width: 0; }}
    .sugg-title {{
      font-size: 13px;
      font-weight: 500;
      color: var(--text-primary);
      line-height: 1.35;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .sugg-tags {{ display: flex; gap: 5px; margin-top: 4px; flex-wrap: wrap; }}
    .tag {{
      font-size: 10px;
      font-weight: 500;
      padding: 1px 7px;
      border-radius: 20px;
      background: var(--bg-soft);
      color: var(--text-tertiary);
      border: 0.5px solid var(--border);
    }}
    .tag.green {{ background: #e8f7ee; color: #1a7a3d; border-color: #b8e0c8; }}
    .tag.blue  {{ background: #e6f0fb; color: #1a5fa5; border-color: #b8d2ee; }}

    /* Footer */
    .ftr {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px 16px;
      border-top: 0.5px solid var(--border);
      gap: 10px;
    }}
    .companion {{
      font-size: 11.5px;
      color: var(--text-tertiary);
      display: flex; align-items: center; gap: 5px;
    }}
    .companion::before {{
      content: '✿';
      color: #c8a4d4;
      font-size: 12px;
    }}
    .ftr-actions {{ display: flex; gap: 8px; }}
    .btn {{
      font-size: 12px;
      font-weight: 500;
      padding: 7px 13px;
      border-radius: 7px;
      border: 0.5px solid var(--border-strong);
      background: var(--bg-card);
      color: var(--text-primary);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .btn:hover {{ background: var(--bg-soft); }}
    .icon-btn {{ padding: 6px 9px; font-size: 13px; line-height: 1; }}
    .quit-btn {{ color: var(--text-tertiary); }}
    .quit-btn:hover {{ color: var(--red); border-color: #f0a8a8; background: #ffeaea; }}

    .arr-up::before {{ content: '↑'; font-size: 9px; }}
    .arr-down::before {{ content: '↓'; font-size: 9px; }}
  </style>
</head>
<body>
  <div class="hud">
    <div class="hdr">
      <div class="hdr-left">
        <span class="{dot_class}"></span>
        <span class="brand">KeyPulse</span>
      </div>
      <div class="hdr-right">
        <span id="weeklyEchoSlot">{weekly_echo_html}</span>
        <span class="{status_pill_class}">{status_label}</span>
      </div>
    </div>

    <div id="hintSlot">{hint_html}</div>
    <div id="weeklyNoticeSlot">{weekly_notice_html}</div>

    <div class="today-card">
      <span class="today-mark">✦</span>
      <input
        id="todayFocus"
        class="{today_input_class}"
        type="text"
        placeholder="今天最想完成的一件事，回车保存"
        autocomplete="off"
        spellcheck="false"
        {f'value="{escape(today_focus, quote=True)}"' if today_focus else ""}
      />
    </div>

    <div class="stats">
      <div id="statsGrid">{stats_html}</div>
    </div>

    <div class="suggestions">
      <div class="sugg-header">今天最新</div>
      <div id="suggestionsSlot">{suggestions_html}</div>
    </div>

    <div class="ftr">
      <span id="companionText" class="companion">{companion_text}</span>
      <div class="ftr-actions">
        {_action_link("↻", "restart-daemon", "btn icon-btn", title="重启 daemon")}
        <a id="togglePauseBtn" class="btn" href="keypulse://action/toggle-pause">{pause_label}</a>
        {_action_link("⏻", "quit", "btn icon-btn quit-btn", title="退出 KeyPulse")}
      </div>
    </div>
  </div>
  <script>
    (function() {{
      var lastSaved = '';
      function safeText(v) {{
        return typeof v === 'string' ? v : '';
      }}
      window.updateHud = function(data) {{
        if (!data || typeof data !== 'object') return;

        var dot = document.querySelector('.dot');
        if (dot) dot.className = safeText(data.dotClass) || 'dot';

        var weeklyEchoSlot = document.getElementById('weeklyEchoSlot');
        if (weeklyEchoSlot) weeklyEchoSlot.innerHTML = safeText(data.weeklyEchoHtml);

        var statusPill = document.querySelector('.status-pill');
        if (statusPill) {{
          statusPill.className = safeText(data.statusPillClass) || 'status-pill';
          statusPill.textContent = safeText(data.statusLabel);
        }}

        var hintSlot = document.getElementById('hintSlot');
        if (hintSlot) hintSlot.innerHTML = safeText(data.hintHtml);

        var weeklyNoticeSlot = document.getElementById('weeklyNoticeSlot');
        if (weeklyNoticeSlot) weeklyNoticeSlot.innerHTML = safeText(data.weeklyNoticeHtml);

        var input = document.getElementById('todayFocus');
        if (input) {{
          var focusVal = safeText(data.todayFocus);
          if (document.activeElement !== input) {{
            input.value = focusVal;
          }}
          input.className = safeText(data.todayInputClass) || 'today-input';
          lastSaved = focusVal;
        }}

        var statsGrid = document.getElementById('statsGrid');
        if (statsGrid) statsGrid.innerHTML = safeText(data.statsHtml);

        var suggestions = document.getElementById('suggestionsSlot');
        if (suggestions) suggestions.innerHTML = safeText(data.suggestionsHtml);

        var pauseBtn = document.getElementById('togglePauseBtn');
        if (pauseBtn) pauseBtn.textContent = safeText(data.pauseLabel);

        var companion = document.getElementById('companionText');
        if (companion) companion.textContent = safeText(data.companionText);

        setTimeout(reportHeight, 0);
      }};

      // Inline input: 回车 / 失焦保存 today_focus
      var input = document.getElementById('todayFocus');
      if (input) {{
        function save() {{
          var v = input.value.trim();
          if (v === lastSaved) return;
          lastSaved = v;
          var url = 'keypulse://action/save-today-focus?v=' + encodeURIComponent(v);
          window.location.href = url;
          input.classList.add('saved-flash');
          setTimeout(function() {{ input.classList.remove('saved-flash'); }}, 700);
        }}
        input.addEventListener('keydown', function(e) {{
          if (e.key === 'Enter') {{ e.preventDefault(); input.blur(); save(); }}
          if (e.key === 'Escape') {{ e.preventDefault(); input.value = lastSaved; input.blur(); }}
        }});
        input.addEventListener('blur', save);
      }}

      // 点 HUD 任意非交互区域 → 关闭 popover
      document.addEventListener('click', function(e) {{
        var t = e.target;
        while (t && t !== document.body) {{
          var tag = (t.tagName || '').toLowerCase();
          if (tag === 'a' || tag === 'button' || tag === 'input' || tag === 'textarea') {{
            return;  // 点的是交互元素，不关
          }}
          t = t.parentNode;
        }}
        e.preventDefault();
        var probe = document.createElement('iframe');
        probe.style.display = 'none';
        probe.src = 'keypulse://action/close-popover';
        document.body.appendChild(probe);
        setTimeout(function() {{ probe.remove(); }}, 50);
      }});

      // Auto-resize: notify host whenever the HUD height changes (hint bar
      // appears, signals refresh, etc.). Host parses ?h=… and resizes popover.
      var lastH = 0;
      function reportHeight() {{
        var h = Math.ceil(document.documentElement.scrollHeight);
        if (h && Math.abs(h - lastH) >= 2) {{
          lastH = h;
          // Use an iframe trick so the URL load doesn't replace the page.
          var probe = document.createElement('iframe');
          probe.style.display = 'none';
          probe.src = 'keypulse://action/resize?h=' + h;
          document.body.appendChild(probe);
          setTimeout(function() {{ probe.remove(); }}, 50);
        }}
      }}
      if (typeof ResizeObserver !== 'undefined') {{
        new ResizeObserver(reportHeight).observe(document.body);
      }}
      var INITIAL_HUD_DATA = {initial_data};
      window.updateHud(INITIAL_HUD_DATA);
      window.addEventListener('load', reportHeight);
    }})();
  </script>
</body>
</html>
"""
