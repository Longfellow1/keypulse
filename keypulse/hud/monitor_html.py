from __future__ import annotations

from html import escape
from urllib.parse import quote

from keypulse.hud.summary import HUDSnapshot


def _action_link(label: str, action: str, *, variant: str = "secondary") -> str:
    return f'<a class="btn btn-{variant}" href="keypulse://action/{quote(action)}">{escape(label)}</a>'


def _signal_link(path: str) -> str:
    return f'keypulse://signal/{quote(path, safe="")}'


def build_monitor_html(snapshot: HUDSnapshot, *, capture_status: str) -> str:
    status_label = "正在采集" if capture_status != "paused" else "已暂停"
    source_rows = "".join(
        f"<div class='source-row'><span>{escape(label)}</span><strong>{count}</strong></div>"
        for label, count in sorted(snapshot.source_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    ) or "<div class='empty'>今天还没有采集来源数据</div>"

    signal_cards = "".join(
        f"""
        <article class="signal-card">
          <div class="signal-source">{escape(signal['source'])}</div>
          <h3>{escape(signal['title'])}</h3>
          <p>{escape(signal['reason'])}</p>
          <a class="btn btn-primary" href="{_signal_link(signal['path'])}">打开详情</a>
        </article>
        """
        for signal in snapshot.top_signals[:4]
    ) or """
        <article class="signal-card empty-card">
          <h3>今天还没有重点内容</h3>
          <p>当前没有高价值候选，可继续采集或手动保存想法。</p>
          <a class="btn btn-primary" href="keypulse://action/open-dashboard">打开工作台</a>
        </article>
    """

    attention_items = "".join(
        f"<a class='tag removable' href='keypulse://action/remove-attention?label={quote(item)}'>{escape(item)}</a>"
        for item in snapshot.attention_items[:6]
    ) or "<div class='empty'>还没有长期关注事项</div>"

    focus_text = escape(snapshot.today_focus or "今天还没有设置今日意图")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KeyPulse 监视器</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: rgba(255,255,255,0.92);
      --card-strong: #ffffff;
      --border: #d8e1ee;
      --text: #17324d;
      --muted: #698099;
      --primary: #2a67d0;
      --primary-soft: #eaf2ff;
      --success: #1f8f63;
      --success-soft: #e8f7ef;
      --warning: #d57d00;
      --warning-soft: #fff4e5;
      --shadow: 0 18px 40px rgba(18, 42, 66, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, #e8f1ff 0, transparent 32%),
        linear-gradient(180deg, #f7f9fc 0%, var(--bg) 100%);
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      display: grid;
      gap: 18px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 16px;
      background: linear-gradient(135deg, #ffffff 0%, #f7fbff 100%);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 10px;
      font-size: 28px;
      line-height: 1.15;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }}
    .hero-side {{
      display: grid;
      gap: 12px;
      align-content: start;
    }}
    .pill-row, .tag-row, .mode-row, .action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .pill, .tag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--text);
      text-decoration: none;
    }}
    .pill-status {{
      background: var(--primary-soft);
      border-color: #bfd4ff;
      color: var(--primary);
    }}
    .pill-source {{
      background: var(--success-soft);
      border-color: #ccead8;
      color: var(--success);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 260px minmax(420px, 1fr) 320px;
      gap: 18px;
      align-items: start;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    .card h2 {{
      margin: 0 0 14px;
      font-size: 16px;
      line-height: 1.25;
    }}
    .metric-grid {{
      display: grid;
      gap: 12px;
    }}
    .metric {{
      background: var(--card-strong);
      border-radius: 18px;
      border: 1px solid var(--border);
      padding: 14px 16px;
    }}
    .metric label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .metric strong {{
      font-size: 28px;
      line-height: 1;
    }}
    .source-list {{
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }}
    .source-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .source-row strong {{
      color: var(--text);
      font-size: 14px;
    }}
    .signal-stack {{
      display: grid;
      gap: 14px;
    }}
    .signal-card {{
      position: relative;
      background: var(--card-strong);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px 18px 16px 22px;
      overflow: hidden;
    }}
    .signal-card::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      width: 4px;
      height: 100%;
      background: linear-gradient(180deg, #2a67d0 0%, #5aa1ff 100%);
    }}
    .signal-source {{
      display: inline-flex;
      margin-bottom: 10px;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--primary-soft);
      color: var(--primary);
      font-size: 12px;
      font-weight: 600;
    }}
    .signal-card h3 {{
      margin: 0 0 10px;
      font-size: 16px;
      line-height: 1.35;
    }}
    .signal-card p {{
      margin: 0 0 14px;
      color: var(--muted);
      line-height: 1.55;
      min-height: 44px;
    }}
    .side-stack {{
      display: grid;
      gap: 14px;
    }}
    .focus {{
      color: var(--muted);
      line-height: 1.6;
      min-height: 48px;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      border: 1px solid transparent;
      transition: opacity 0.15s ease;
    }}
    .btn:hover {{ opacity: 0.88; }}
    .btn-primary {{
      background: var(--primary);
      color: white;
    }}
    .btn-secondary {{
      background: #fff;
      color: var(--text);
      border-color: var(--border);
    }}
    .btn-warning {{
      background: var(--warning-soft);
      color: var(--warning);
      border-color: #f3d4a2;
    }}
    .empty {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    .removable {{
      background: #fff;
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div>
        <div class="pill-row">
          <span class="pill pill-status">{escape(snapshot.mode_label)} · {escape(status_label)}</span>
          <span class="pill">最近同步：{escape(str(snapshot.last_sync_at or "—"))}</span>
        </div>
        <h1>今天的 KeyPulse</h1>
        <p>{escape(snapshot.summary_line)}</p>
      </div>
      <div class="hero-side">
        <strong>正文来源</strong>
        <div class="pill-row">
          {"".join(
            f"<span class='pill pill-source'>{escape(name)}</span>"
            for name, enabled in snapshot.active_sources.items()
            if enabled
          ) or "<span class='pill'>尚未启用正文来源</span>"}
        </div>
      </div>
    </section>

    <section class="layout">
      <aside class="card">
        <h2>今日概览</h2>
        <div class="metric-grid">
          <div class="metric"><label>有效内容</label><strong>{snapshot.effective_count}</strong></div>
          <div class="metric"><label>已过滤噪音</label><strong>{snapshot.filtered_count}</strong></div>
          <div class="metric"><label>候选主题</label><strong>{snapshot.theme_count}</strong></div>
          <div class="metric"><label>手动标记</label><strong>{snapshot.manual_marked_count}</strong></div>
        </div>
        <div class="source-list">{source_rows}</div>
      </aside>

      <section class="card">
        <h2>今日重点流</h2>
        <div class="signal-stack">{signal_cards}</div>
      </section>

      <aside class="side-stack">
        <section class="card">
          <h2>今日意图</h2>
          <div class="focus">{focus_text}</div>
          <div class="action-row">
            {_action_link("编辑今日意图", "set-focus", variant="primary")}
            {_action_link("清空", "clear-focus")}
          </div>
        </section>

        <section class="card">
          <h2>额外关注事项</h2>
          <div class="tag-row">{attention_items}</div>
          <div class="action-row" style="margin-top:14px;">
            {_action_link("新增关注事项", "add-attention", variant="primary")}
          </div>
        </section>

        <section class="card">
          <h2>模式与动作</h2>
          <div class="mode-row">
            {_action_link("标准", "mode-standard")}
            {_action_link("专注", "mode-focus")}
            {_action_link("高敏", "mode-sensitive")}
            {_action_link("回顾", "mode-review")}
          </div>
          <div class="action-row" style="margin-top:14px;">
            {_action_link("打开工作台", "open-dashboard", variant="primary")}
            {_action_link("健康状态", "open-health")}
          </div>
          <div class="action-row" style="margin-top:10px;">
            {_action_link("保存想法", "save-thought")}
            {_action_link("标记当前窗口", "mark-window")}
            {_action_link("暂停或恢复", "toggle-pause", variant="warning")}
          </div>
        </section>
      </aside>
    </section>
  </main>
</body>
</html>
"""
