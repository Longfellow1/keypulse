from __future__ import annotations

from datetime import date
from html import escape
from urllib.parse import quote

from keypulse.hud.summary import HUDSnapshot


def _action_link(label: str, action: str, class_name: str, *, title: str | None = None) -> str:
    title_attr = f' title="{escape(title)}"' if title else ""
    return f'<a class="{class_name}" href="keypulse://action/{quote(action)}"{title_attr}>{escape(label)}</a>'


def _mode_badge(mode_label: str) -> str:
    value = (mode_label or "标准").strip()
    return value.removesuffix("模式") or value


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


def _day_number(date_str: str) -> str:
    try:
        return str(date.fromisoformat(date_str).day)
    except ValueError:
        return "1"


def build_monitor_html(snapshot: HUDSnapshot, *, capture_status: str, health_ok: bool = True) -> str:
    is_running = capture_status != "paused"
    dot_class = "hdr-dot ok" if is_running and health_ok else "hdr-dot"
    pause_label = "⏸ 暂停" if is_running else "▶ 恢复"
    health_label = "系统正常" if health_ok else "系统需检查"
    suggestions = "".join(_signal_item(signal) for signal in snapshot.top_signals[:3]) or _empty_signal_item()

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KeyPulse HUD</title>
  <style>
    :root {{
      --color-background-primary: #ffffff;
      --color-background-secondary: #f6f6f7;
      --color-border-secondary: rgba(60, 60, 67, 0.18);
      --color-border-tertiary: rgba(60, 60, 67, 0.12);
      --color-text-primary: #1d1d1f;
      --color-text-secondary: rgba(60, 60, 67, 0.68);
      --color-text-tertiary: rgba(60, 60, 67, 0.46);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ width: 280px; height: 320px; overflow: hidden; background: transparent; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    a {{ text-decoration: none; -webkit-user-drag: none; }}
    .hud-after {{
      width: 280px;
      background: var(--color-background-primary);
      border: 0.5px solid var(--color-border-secondary);
      border-radius: 14px;
      overflow: hidden;
      height: 320px;
      font-size: 13px;
      color: var(--color-text-primary);
      display: flex;
      flex-direction: column;
    }}

    .hud-after .hdr {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px 10px;
      border-bottom: 0.5px solid var(--color-border-tertiary);
    }}
    .hdr-left {{ display: flex; align-items: center; gap: 7px; min-width: 0; }}
    .hdr-dot {{
      width: 7px; height: 7px;
      border-radius: 50%;
      background: #f5a623;
      flex-shrink: 0;
    }}
    .hdr-dot.ok {{ background: #34c759; }}
    .hdr-name {{ font-size: 13px; font-weight: 500; color: var(--color-text-primary); }}
    .hdr-mode {{
      font-size: 10px;
      font-weight: 500;
      padding: 2px 7px;
      border-radius: 20px;
      background: var(--color-background-secondary);
      color: var(--color-text-secondary);
      border: 0.5px solid var(--color-border-tertiary);
      white-space: nowrap;
    }}
    .hdr-pause {{
      font-size: 11px;
      color: var(--color-text-tertiary);
      cursor: pointer;
      padding: 3px 7px;
      border-radius: 5px;
      border: 0.5px solid var(--color-border-tertiary);
      white-space: nowrap;
    }}

    .hud-after .stats {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr 1fr;
      gap: 0;
      border-bottom: 0.5px solid var(--color-border-tertiary);
    }}
    .stat-cell {{
      padding: 10px 0 9px;
      text-align: center;
      border-right: 0.5px solid var(--color-border-tertiary);
      min-width: 0;
    }}
    .stat-cell:last-child {{ border-right: none; }}
    .stat-num {{
      font-size: 17px;
      font-weight: 500;
      color: var(--color-text-primary);
      line-height: 1;
      white-space: nowrap;
    }}
    .stat-num .delta {{ font-size: 10px; font-weight: 400; color: #34c759; }}
    .stat-num .delta.down {{ color: #ff3b30; }}
    .stat-num .delta.flat {{ color: var(--color-text-tertiary); }}
    .stat-lbl {{ font-size: 10px; color: var(--color-text-tertiary); margin-top: 3px; }}

    .hud-after .suggestions {{
      padding: 10px 14px;
      display: flex;
      flex-direction: column;
      gap: 1px;
      border-bottom: 0.5px solid var(--color-border-tertiary);
      flex: 1;
    }}
    .sugg-header {{
      font-size: 10px;
      font-weight: 500;
      color: var(--color-text-tertiary);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .sugg-item {{
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 7px 9px;
      border-radius: 7px;
      cursor: default;
      transition: background 0.1s;
    }}
    .sugg-item:hover {{ background: var(--color-background-secondary); }}
    .sugg-icon {{
      width: 20px; height: 20px;
      border-radius: 5px;
      background: var(--color-background-secondary);
      border: 0.5px solid var(--color-border-tertiary);
      flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 10px;
      color: var(--color-text-secondary);
    }}
    .sugg-text {{ flex: 1; min-width: 0; }}
    .sugg-title {{
      font-size: 12px;
      font-weight: 500;
      color: var(--color-text-primary);
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .sugg-tags {{ display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; }}
    .tag {{
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 20px;
      background: var(--color-background-secondary);
      color: var(--color-text-tertiary);
      border: 0.5px solid var(--color-border-tertiary);
    }}
    .tag.green {{ background: #eaf9f0; color: #1a7a3d; border-color: #b2dfc5; }}
    .tag.blue {{ background: #eaf3fb; color: #1a5fa5; border-color: #afd0ef; }}

    .hud-after .actions {{
      padding: 10px 14px;
      display: flex;
      gap: 7px;
      border-bottom: 0.5px solid var(--color-border-tertiary);
    }}
    .act-primary {{
      flex: 1;
      font-size: 12px;
      font-weight: 500;
      padding: 8px;
      border-radius: 7px;
      background: var(--color-text-primary);
      color: var(--color-background-primary);
      border: none;
      cursor: pointer;
      text-align: center;
    }}
    .act-secondary {{
      flex: 1;
      font-size: 12px;
      padding: 8px;
      border-radius: 7px;
      background: var(--color-background-secondary);
      color: var(--color-text-primary);
      border: 0.5px solid var(--color-border-tertiary);
      cursor: pointer;
      text-align: center;
    }}

    .hud-after .ftr {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 7px 14px;
    }}
    .ftr-day {{ font-size: 11px; color: var(--color-text-tertiary); }}
    .ftr-quit {{ font-size: 11px; color: var(--color-text-tertiary); cursor: pointer; }}
    .ftr-quit:hover {{ color: #ff3b30; }}
    .ftr-health {{ color: inherit; }}

    .arr-up::before {{ content: '↑'; font-size: 9px; }}
    .arr-down::before {{ content: '↓'; font-size: 9px; }}
  </style>
</head>
<body>
  <div class="hud-after">
    <div class="hdr">
      <div class="hdr-left">
        <div class="{dot_class}"></div>
        <span class="hdr-name">KeyPulse</span>
        <span class="hdr-mode">{escape(_mode_badge(snapshot.mode_label))}</span>
      </div>
      {_action_link(pause_label, "toggle-pause", "hdr-pause")}
    </div>

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

    <div class="actions">
      {_action_link("保存想法", "save-thought", "act-primary")}
      {_action_link("设意图", "set-intent", "act-secondary")}
    </div>

    <div class="ftr">
      <span class="ftr-day">今天第 {_day_number(snapshot.date)} 天 · {_action_link(health_label, "show-health", "ftr-health")}</span>
      {_action_link("退出", "quit", "ftr-quit")}
    </div>
  </div>
</body>
</html>
"""
