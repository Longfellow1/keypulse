from __future__ import annotations

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
    tags = "".join(f'<span class="{_tag_class(tag)}">{escape(tag)}</span>' for tag in _signal_tags(reason))
    return f"""
        <div class="sugg-item">
          <div class="sugg-icon">{escape(_signal_icon(title))}</div>
          <div class="sugg-text">
            <div class="sugg-title">{escape(title)}</div>
            <div class="sugg-tags">{tags}</div>
          </div>
        </div>
    """


def _empty_signal_item() -> str:
    return """
        <div class="sugg-item">
          <div class="sugg-icon">#</div>
          <div class="sugg-text">
            <div class="sugg-title">今天还没有重点提示</div>
            <div class="sugg-tags"><span class="tag">继续采集</span></div>
          </div>
        </div>
    """


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
    value_attr = f' value="{escape(today_focus, quote=True)}"' if today_focus else ""
    filled_cls = " filled" if today_focus else ""
    return f"""
      <div class="today-card">
        <div class="today-label">今日重要的事儿</div>
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


def build_monitor_html(snapshot: HUDSnapshot, *, capture_status: str, health_ok: bool = True) -> str:
    is_running = capture_status != "paused"
    pause_label = "⏸ 暂停" if is_running else "▶ 恢复"
    suggestions = "".join(_signal_item(signal) for signal in snapshot.top_signals[:3]) or _empty_signal_item()
    dot_cls = _dot_class(snapshot.service_status)
    pill_cls = _status_pill_class(snapshot.service_status)

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

    /* 今日重要的事儿 */
    .today-card {{
      display: block;
      margin: 0 18px 14px;
      padding: 12px 13px;
      background: var(--bg-input);
      border: 0.5px solid var(--border);
      border-radius: 10px;
      cursor: pointer;
      transition: background 0.1s;
    }}
    .today-card:hover {{ background: #f0f0f3; }}
    .today-label {{
      font-size: 11px;
      font-weight: 600;
      color: var(--text-tertiary);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 6px;
      display: flex; align-items: center; gap: 6px;
    }}
    .today-label::before {{
      content: '✦';
      color: var(--blue);
      font-size: 13px;
      letter-spacing: 0;
      text-transform: none;
    }}
    .today-input {{
      font-size: 13px;
      color: var(--text-primary);
      line-height: 1.5;
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
      grid-template-columns: 1fr 1fr 1fr 1fr;
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
      align-items: flex-start;
      gap: 10px;
      padding: 8px 10px;
      margin: 0 -10px;
      border-radius: 8px;
    }}
    .sugg-item:hover {{ background: var(--bg-soft); }}
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
        <span class="{dot_cls}"></span>
        <span class="brand">KeyPulse</span>
      </div>
      <span class="{pill_cls}">{escape(snapshot.status_label)}</span>
    </div>

    {_hint_bar(snapshot.hint_message, snapshot.hint_action)}

    {_today_card(snapshot.today_focus)}

    <div class="stats">
      {_stat_cell("有效", snapshot.effective_count, snapshot.effective_count_delta_vs_yesterday)}
      {_stat_cell("过滤", snapshot.filtered_count, snapshot.filtered_count_delta_vs_yesterday)}
      {_stat_cell("主题", snapshot.theme_count, snapshot.theme_count_delta_vs_yesterday)}
      {_stat_cell("标记", snapshot.manual_marked_count, snapshot.manual_marked_count_delta_vs_yesterday)}
    </div>

    <div class="suggestions">
      <div class="sugg-header">今日提示</div>
      {suggestions}
    </div>

    <div class="ftr">
      <span class="companion">和你一起记录的第 {snapshot.companion_days} 天</span>
      <div class="ftr-actions">
        {_action_link("↻", "restart-daemon", "btn icon-btn", title="重启 daemon")}
        {_action_link(pause_label, "toggle-pause", "btn")}
        {_action_link("⏻", "quit", "btn icon-btn quit-btn", title="退出 KeyPulse")}
      </div>
    </div>
  </div>
  <script>
    (function() {{
      var input = document.getElementById('todayFocus');
      if (input) {{
        var lastSaved = input.value || '';
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
      window.addEventListener('load', reportHeight);
    }})();
  </script>
</body>
</html>
"""
