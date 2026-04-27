"""
骨架 MVP（叙事骨架 v0）

核心思路（不是流水账）：
  - 一天开始时，预设 7 个"使用电脑的动机"假设
  - 每次跑：让 LLM 看当天的活动证据，对每个动机评 0-1 置信度 + 支持证据 + 缺口
  - 输出：围绕高置信度动机组织的报告

这是孤立外挂脚本，不接 narrative_v2 / write.py / exporter.py 的任何链路。
跑法：python scripts/skeleton_mvp.py --date 2026-04-24
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# ============================================================
# 7 大使用动机定义（产品层）
# ============================================================

MOTIVES = [
    {
        "id": "create",
        "name": "创造",
        "desc": "产出新东西。写代码、写文档、画图、做设计、写下原本不存在的内容。",
        "signals": ["代码编辑", "新文件", "长文输入", "git commit", "画图工具"],
    },
    {
        "id": "understand",
        "name": "理解",
        "desc": "吸收信息以建立认知。读、查、研究、问 AI、看文档、读代码。",
        "signals": ["浏览网页", "看文档", "翻 PR/code", "和 AI 问答", "搜索"],
    },
    {
        "id": "communicate",
        "name": "沟通",
        "desc": "和他人交换信息。聊天、邮件、视频会议、回复消息。",
        "signals": ["IM 应用", "邮件", "会议软件", "群聊"],
    },
    {
        "id": "decide",
        "name": "决策",
        "desc": "在多个选项里拍板。对比、评估、权衡、最后选一个方案。",
        "signals": ["A/B/C 选项讨论", "trade-off 关键词", "对比文档", "选型表"],
    },
    {
        "id": "maintain",
        "name": "维护",
        "desc": "处理已有的事。修 bug、整理文件、重启服务、回邮件、更新工具。",
        "signals": ["debug 命令", "log 查看", "tail/grep", "shell 操作", "fix"],
    },
    {
        "id": "transact",
        "name": "事务",
        "desc": "日常处理。买东西、订票、查账、报销、填表。",
        "signals": ["购物站点", "订票/订房", "银行/支付", "表单"],
    },
    {
        "id": "leisure",
        "name": "消遣",
        "desc": "放松/娱乐。刷视频、看推、玩游戏、社交。",
        "signals": ["视频网站", "微博/Twitter", "游戏", "短视频"],
    },
]


# ============================================================
# Few-shot 示例（从仓库历史里挑的真实风格的样本，标注好动机）
# ============================================================

FEW_SHOT = """
[示例 1]
活动：01:15-01:21 在终端 SSH 到远程机器，跑 ./stats.sh，输出 "No such file or directory"，复制了一段
log 路径 /mnt/paas/.../feishu_agent.log
判定：
  - maintain: 0.85（在调一个已有项目 Code-RAGFlow 的运行状态，复制 log 路径准备查看）
  - understand: 0.5（试图弄清现状，但还没成型）
  - 其他: 低

[示例 2]
活动：09:46 在 Chrome 里打开 Claude 对话，连续 6 次输入"Haiku 给的研究很扎实，我把它压成对我们落地最关键的判断"
判定：
  - decide: 0.7（在做"用什么落地"的决策，trade-off 关键词出现）
  - understand: 0.6（在消化 Haiku 给的内容）
  - communicate: 0.4（和 AI 协作）

[示例 3]
活动：14:00-15:30 在 VSCode 里 fragments.py 大段编辑，期间 git commit 2 次："fix(evidence)" 和 "feat(pipeline)"
判定：
  - create: 0.9（产出新代码，commit 是硬证据）
  - maintain: 0.5（其中一个是 fix）
  - 其他: 低
"""


# ============================================================
# 数据拉取 + 5 分钟聚合
# ============================================================

def fetch_activities(db_path: Path, date_str: str) -> list[dict]:
    """拉当天 raw_events，按 5min 窗 + app 聚合成"活动"。
    返回 [{idx, ts, app, types, samples (拼接的内容片段)}, ...]"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ts_start, event_type, app_name, window_title, content_text
        FROM raw_events
        WHERE date(ts_start)=?
          AND event_type IN ('keyboard_chunk_capture','clipboard_copy','ax_text_capture','window_focus_session')
          AND content_text IS NOT NULL
          AND length(content_text) >= 5
        ORDER BY ts_start
        """,
        (date_str,),
    ).fetchall()
    conn.close()

    buckets: dict[tuple, dict] = {}
    for r in rows:
        ts = datetime.fromisoformat(r["ts_start"].replace("Z", "+00:00"))
        # 5min bucket
        bucket_min = ts.minute - (ts.minute % 5)
        key = (ts.strftime(f"%H:{bucket_min:02d}"), r["app_name"] or "-")
        if key not in buckets:
            buckets[key] = {
                "ts": key[0],
                "app": key[1],
                "types": set(),
                "samples": [],
                "title": r["window_title"] or "",
            }
        buckets[key]["types"].add(r["event_type"])
        # 留前 60 字够 prompt 用
        snippet = (r["content_text"] or "")[:60].replace("\n", " ")
        if snippet and snippet not in buckets[key]["samples"]:
            buckets[key]["samples"].append(snippet)

    activities = []
    for i, (key, b) in enumerate(sorted(buckets.items())):
        if not b["samples"]:
            continue
        activities.append({
            "idx": i,
            "ts": b["ts"],
            "app": b["app"],
            "types": sorted(b["types"]),
            "title": b["title"][:40],
            "samples": b["samples"][:3],  # 每 bucket 最多 3 条样本
        })
    return activities


def render_activities_for_prompt(activities: list[dict]) -> str:
    lines = []
    for a in activities:
        type_str = "/".join(t.replace("_capture", "").replace("_copy", "") for t in a["types"])
        samples_str = " | ".join(a["samples"])
        lines.append(f"[{a['idx']}] {a['ts']} app={a['app']} ({type_str}): {samples_str}")
    return "\n".join(lines)


# ============================================================
# LLM 调用（Baidu AI Studio，OpenAI compatible）
# ============================================================

def call_llm(prompt: str) -> str:
    api_key = os.environ.get("BAIDU_AISTUDIO_API_KEY", "")
    if not api_key:
        # 尝试从 secrets.env 读
        secrets_path = Path(os.path.expanduser("~/.keypulse/secrets.env"))
        if secrets_path.exists():
            for line in secrets_path.read_text().splitlines():
                if line.startswith("export BAIDU_AISTUDIO_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        raise SystemExit("missing BAIDU_AISTUDIO_API_KEY")

    payload = {
        "model": "ernie-4.5-turbo-128k",
        "messages": [
            {"role": "system", "content": "你是一个精准的语义分析器。只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    req = Request(
        "https://aistudio.baidu.com/llm/lmapi/v3/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"LLM HTTP {e.code}: {body[:300]}")

    return data["choices"][0]["message"]["content"]


def build_prompt(activities_text: str) -> str:
    motives_text = "\n".join(
        f"- {m['id']} ({m['name']})：{m['desc']} 信号：{', '.join(m['signals'])}"
        for m in MOTIVES
    )
    return f"""任务：根据下面一天的电脑活动证据，对 7 个"使用动机"假设做置信度评估。

# 7 个动机假设
{motives_text}

# Few-shot 示例（看动机怎么打分）
{FEW_SHOT}

# 今天的活动（每条编号为 [idx]）
{activities_text}

# 输出格式（严格 JSON，不要 markdown 代码围栏）

{{
  "motives": [
    {{
      "id": "<动机 id>",
      "confidence": <0-1>,
      "summary": "<一句话讲今天这个动机表现成什么样，第二人称>",
      "evidence_idx": [<引用上面的 idx>],
      "gap": "<还缺什么证据才能更确定，没缺口写空串>"
    }}, ...所有 7 个
  ],
  "main_lines": ["<2-3 个高置信度动机串成的今日主线，每条一句>"]
}}

只对真有证据支持的动机给 ≥0.5 置信度，其余给 0-0.3。
evidence_idx 只引用真支持当前动机的，不要为了凑数。
不要写"今日证据不足"——你的工作是从已有证据里找到动机骨架，证据稀少时降低置信度即可。"""


# ============================================================
# 报告渲染
# ============================================================

def render_report(date_str: str, activities: list[dict], analysis: dict) -> str:
    motive_name = {m["id"]: m["name"] for m in MOTIVES}
    sorted_motives = sorted(
        analysis.get("motives", []),
        key=lambda m: m.get("confidence", 0),
        reverse=True,
    )

    lines = [f"# {date_str} 骨架报告（MVP）", ""]

    # 主线
    lines.append("## 今日主线")
    for ml in analysis.get("main_lines", []):
        lines.append(f"- {ml}")
    lines.append("")

    # 高置信度动机详写
    lines.append("## 动机骨架")
    for m in sorted_motives:
        name = motive_name.get(m["id"], m["id"])
        conf = m.get("confidence", 0)
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        lines.append(f"### {name} {bar} {conf:.2f}")
        lines.append(f"**{m.get('summary', '')}**")
        lines.append("")
        if m.get("evidence_idx"):
            lines.append("证据：")
            for idx in m["evidence_idx"][:5]:
                a = next((x for x in activities if x["idx"] == idx), None)
                if a:
                    sample = " | ".join(a["samples"][:2])[:120]
                    lines.append(f"- [{a['ts']}] {a['app']} — {sample}")
        if m.get("gap"):
            lines.append(f"\n*缺口*：{m['gap']}")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db", default=os.path.expanduser("~/.keypulse/keypulse.db"))
    ap.add_argument("--max-activities", type=int, default=80, help="塞给 LLM 的活动条数上限")
    ap.add_argument("--out", default="", help="报告输出路径，默认打印到 stdout")
    args = ap.parse_args()

    db_path = Path(args.db)
    activities = fetch_activities(db_path, args.date)
    print(f"[info] {args.date}: 拉到 {len(activities)} 条活动 buckets", file=sys.stderr)

    if len(activities) > args.max_activities:
        # 均匀降采样
        step = len(activities) // args.max_activities
        activities = activities[::max(1, step)][: args.max_activities]
        # 重编号
        for i, a in enumerate(activities):
            a["idx"] = i
        print(f"[info] 降采样到 {len(activities)} 条", file=sys.stderr)

    activities_text = render_activities_for_prompt(activities)
    prompt = build_prompt(activities_text)
    print(f"[info] prompt 长度 {len(prompt)} 字符", file=sys.stderr)

    raw_response = call_llm(prompt)
    # 容错：从原始响应里抓第一个 { 到匹配的最后 }
    text = raw_response.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        print(f"[error] 找不到 JSON 主体\n[raw]\n{raw_response}", file=sys.stderr)
        sys.exit(1)
    cleaned = text[start : end + 1]
    try:
        analysis = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[error] JSON 解析失败: {e}\n[cleaned]\n{cleaned[:500]}", file=sys.stderr)
        sys.exit(1)

    report = render_report(args.date, activities, analysis)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"[info] 写入 {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
