# Obsidian 日报重设计方案

- 状态：待拍板
- 日期：2026-04-20
- 作者：Harland（设计） + Claude（调研与起草）
- 影响范围：`keypulse/pipeline/` + `keypulse/obsidian/` + `keypulse/config.py`

---

## 1. 背景

KeyPulse 目前会把每日采集的数据通过 "pipeline" 管线导出到 Obsidian 金库。输出的核心产物是 Dashboard（今日工作台）和 Daily（日笔记）两类 markdown。

用户反馈：**读完这份报告，没有"今天发生了什么、我该做什么"的感觉**。本文档诊断现状、重新设计生成管线与模板、列出落地改动清单。

---

## 2. 诊断：现状出了什么问题

### 2.1 LLM 被当成 markdown linter 在用

`keypulse/pipeline/write.py:64-71` 里 prompt 的全文：

```python
prompt = (
    "Normalize the markdown without changing meaning. "
    "Keep bullets, headings, and code fences intact.\n\n"
    f"{text.strip()}"
)
```

这不是生成日报，是格式化。真正的摘要、行动项提取、重点突出完全没有发生。

### 2.2 打分器内部细节直接暴露

`exporter.py:175-231` 把 candidate 的 `why_selected` 字典（`explicitness / novelty / reusability / decision_signal / density / recurrence`）原封不动渲染给用户。用户没有心智模型去解读这些打分项，看到的只是一串专业名词。

### 2.3 "建议你处理" 是同一句模板

目前所有候选都套一个模板：`检查 X 是否该升级为主题，或并入已有主题`。这既没有基于历史数据给出判断，也没有区分"值得升级 / 一次性 / 明日继续"这类差异。

### 2.4 数据库里大量有用字段未被使用

调研（见 §3）显示 `raw_events` 和 `sessions` 表里有以下字段**从未进入报告生成链路**：

- `ts_end` —— 事件时长
- `session_id` + `sessions.duration_sec` —— 工作块聚合
- `sessions.primary_window_title` —— 工作块"战场"标签
- `sensitivity_level` —— 隐私标记
- `skipped_reason` —— 过滤透明化

这些字段是把"时间线叙述"和"专注时段"做出来的关键原料。

### 2.5 反馈完全不闭环

`keypulse/pipeline/feedback.py` 的 `read_feedback_events()` 定义了读反馈的接口，**但从未被 `write.py` / `aggregate.py` 调用过**。用户标记 "promote / skip / note" 没有任何地方回流到下一次生成。

### 2.6 LLM 默认关闭

`config.py:61` 的 `llm_mode` 默认值是 `"off"`。即使接了云端 OpenAI-compatible 后端（`config.py:76-92`），大多数用户实际跑的是纯模板渲染路径。"报告不好读" 很大一部分根源是 LLM 根本没开。

---

## 3. 数据字段盘点（作为重设计的素材）

所有字段名均来自代码扫描，无编造。

### 3.1 Surface snapshot 结构

来源：`keypulse/pipeline/surface.py`。`build_surface_snapshot()` 返回值：

| 顶层字段 | 类型 | 说明 |
|---|---|---|
| `filtered_total` | int | 被过滤事件总数 |
| `filtered_reasons` | dict[str, int] | `idle_event / low_signal_window / low_density_fragment` |
| `candidates` | list[dict] | 前 10 高分候选（按 score 降序） |
| `theme_candidates` | list[dict] | 按主题分组的候选 |

candidate 对象字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `score` | float | 0.0–1.5，综合评分 |
| `title` | str | 优先级：title > window_title > content_text[:72] > event_type |
| `source` | str | `manual / clipboard / window / ax_text / ocr_text / keyboard_chunk` |
| `event_type` | str | `window_focus / clipboard_copy / manual_save` 等 |
| `topic_key` | str \| None | 主题键（如 `开发-工具-调试`） |
| `tags` | list[str] | 用户自定义标签 |
| `evidence` | str | 原始内容 |
| `why_selected` | dict[str, float] | 打分分项 |

theme_candidate 字段：`key / topic_key / item_count / avg_score / top_evidence`。

### 3.2 未被利用、但在落地后要用的字段

| 来源表 | 字段 | 计划用途 |
|---|---|---|
| `raw_events` | `ts_end` | 事件时长 → 叙述"下午 14:02 花了 18 分钟整理" |
| `raw_events` | `session_id` | 聚合工作块（§5.2） |
| `raw_events` | `process_name` | 区分工具版本 / 识别应用族群 |
| `raw_events` | `sensitivity_level` | 高敏事件报告里隐藏内容，仅显示"已记录但不展示" |
| `sessions` | `duration_sec` | 工作块专注时长 |
| `sessions` | `primary_window_title` | 工作块"战场"名称 |
| `sessions` | `event_count` | 专注深度 vs 碎片化判断 |

---

## 4. 设计原则

| 原则 | 含义 |
|---|---|
| **叙述优于枚举** | 让用户读完能回忆起今天。列表只用于"需要扫一眼"的地方 |
| **打分翻译成人话** | 用户不该看见 `explicitness: 0.3`。该看见"你主动保存" |
| **决策权明确交回用户** | 每份报告只产出 2-3 个"需要你拍板的事"，每个给出具体命令 |
| **时间是一等公民** | 时间块 / 时长 / 占比 / 工作块边界，都要入报告 |
| **历史连接** | "上周还有 3 条分散记录" 这种跨日信息是"记忆感"的来源 |
| **可溯源** | 所有叙述里的时间点都要能点回具体 Event 卡 |
| **降级不崩溃** | LLM 不可用时，退化成规则版的叙述仍能读 |

---

## 5. 新报告架构

### 5.1 四层结构

| 层 | 作用 | 长度 | 谁生成 |
|---|---|---|---|
| 1. Frontmatter + TL;DR | 扫一眼就走 | 3 行 | 规则 + 数值统计 |
| 2. 今日主线叙述 | 代入感、可回忆 | 2-4 段 | LLM，第二人称 |
| 3. 需要你决定 | 明确行动项 | 1-3 项 | LLM，融合今日 + 历史 |
| 4. 附录（折叠） | 可溯源 | 表格 | 数据直出 |

### 5.2 工作块聚合算法（新增 `pipeline/narrative.py`）

把零散事件聚合成"工作块"，这是叙述的最小单位。

```
WorkBlock = {
    theme: str,              # 主题（来自 candidate.topic_key 或启发式）
    duration_sec: int,       # 工作块时长
    ts_start / ts_end: str,  # 起止时间
    primary_app: str,        # 主要应用
    event_count: int,
    key_candidates: list,    # 块内高分候选（用于叙述引用）
    continuity: "new" | "continued" | "returned"  # 与近 7 日是否连续
}
```

聚合规则：
1. 按 `session_id` 切一级块
2. 同 session 内若 `topic_key` 相同则合并；不同 topic 的子块保留
3. `continuity` 判断：查近 7 日是否有同 `topic_key` 的候选 → `continued`（昨天也在做）/ `returned`（隔天回来）/ `new`（首次）
4. `duration_sec < 300` 的工作块合并成 "碎片"，不单独叙述

### 5.3 叙述 prompt（替换 `write.py:64-71`）

```python
SYSTEM_PROMPT = """你是用户的知识库助理。任务：根据结构化的"工作块"数据，
写一段第二人称的日报叙述，让用户读完能回忆起今天做了什么、为什么重要。

规则：
1. 每个工作块写 1 段，按时间顺序
2. 引用具体时间（HH:MM）和应用名
3. 提到具体证据时用 [[Events/时间-标题]] 链接格式
4. 如果 continuity=continued/returned，明说"继续昨天的 X"或"隔了几天回到 X"
5. 不要列要点，写连贯的散文
6. 不要夸张（"非常棒"、"重要突破"之类），克制、事实
7. 不要暴露打分数值，用人话："你主动圈选"、"今日首次"、"最近第 N 次"
"""

USER_PROMPT = """工作块数据（JSON）：
{work_blocks_json}

请输出这一天的叙述，约 150-300 字。"""
```

### 5.4 "需要你决定" 生成逻辑（新增 `pipeline/decisions.py`）

输入：今日 candidates + `feedback.jsonl`（近 30 天） + themes profile。

对每个高分候选，走判定树：

```
if candidate.topic_key exists in themes:
    if 今日 + 近 7 日 新增证据 ≥ 3: 建议 → "已立主题，继续记录"
    else: 建议 → "归入已有主题"
elif 今日 + 近 7 日 相关候选数 ≥ 4:
    建议 → "升级为主题，给出命令"
elif candidate.source == "manual":
    建议 → "你主动保存，建议加标签归档"
elif candidate 是 session 内最后一个未结束事件:
    建议 → "明日继续看，给出起点 Event 链接"
else:
    建议 → "一次性输入，建议加标签归档"
```

每条建议配具体命令：`keypulse pipeline feedback promote/defer/archive <target>`。

限制：每日最多输出 3 条决策，避免决策疲劳。

### 5.5 打分翻译表（新增 `pipeline/surface.py::translate_why_selected`）

| 内部字段 | 阈值 | 人话标签 |
|---|---|---|
| `explicitness` | ≥ 0.25 | 你主动保存 |
| `novelty` | ≥ 0.6 | 今日首次出现 |
| `recurrence` | ≥ 0.5 | 最近第 N 次提及（N 来自 candidate.metadata） |
| `decision_signal` | ≥ 0.4 | 含决策信号（动词"决定/改为/不再"） |
| `density` | ≥ 0.5 | 内容密度高 |
| `reusability` | ≥ 0.4 | 可复用（代码/配置/链接） |

规则：每个 candidate 最多显示 2 个标签，按权重降序。

### 5.6 反馈闭环（修改 `aggregate.py` + `write.py`）

生成前把近 30 天的 feedback 注入 LLM system prompt：

```python
feedback_summary = summarize_feedback(read_feedback_events(days=30))
# e.g. "用户在过去一个月 promote 了 'ReAct 范式' 和 '产品管理';
#       archive 了 'github 随手剪贴'; 多次 defer 'VSCode 配置调研'"

system_prompt += f"\n\n用户历史反馈摘要：\n{feedback_summary}"
```

具体闭环效果：
- 若某主题过去被 promote → 叙述中优先展开
- 若某主题被反复 defer → 决策层提示 "这件事已经 defer 3 次，要不要归档"
- 若某来源被 archive → 降低其进入决策层的权重

---

## 6. 输出对比样例

### 6.1 Before（当前实际输出）

```markdown
---
type: dashboard
date: 2026-04-20
candidate_count: 3
filtered_total: 12
---

# 今日工作台

- 日期：2026-04-20
- 候选内容：3
- 已过滤噪音：12

## 今天最值得看的内容

- ReAct 论文核心思想：协调推理和行动的新范式
  - 价值分：1.18
  - 来源：手动保存
  - 为什么保留：explicitness, novelty, decision_signal, density

- 下午会议笔记：Q1 工程计划重点调整
  - 价值分：1.02
  - 来源：剪贴板
  - 为什么保留：novelty, decision_signal, density

- VSCode 代码补全配置
  - 价值分：0.89
  - 来源：窗口活动
  - 为什么保留：novelty, reusability, density

## 已自动过滤的内容

- 空闲事件：6
- 低密度碎片：4
- 低信号窗口：2

## 建议你处理

- 处理建议：检查"ReAct 论文核心思想：协调推理和行动的新范式"是否该升级为主题，或并入已有主题
- 处理建议：检查"下午会议笔记：Q1 工程计划重点调整"是否该升级为主题，或并入已有主题
- 处理建议：检查"VSCode 代码补全配置"是否该升级为主题，或并入已有主题
```

### 6.2 After（新版，同一天数据）

```markdown
---
date: 2026-04-20
weekday: 周一
focus_hours: 5h18m
context_blocks: 3
top_theme: AI 论文研究
needs_decision: 2
vault: KeyPulse
---

# 2026-04-20 · 周一

> **主战场是 ReAct 范式**。下午被 Q1 计划会议插了 50 分钟。晚上在调研 VSCode 补全替代品，没结。
> 今天有 **2 件事等你拍板**：ReAct 要不要立长期主题 / VSCode 调研明天接着看不看。

## 🎯 今日主线

### AI 论文研究 · 2h14m（占今天 42%）

上午 10:23 在 Safari 打开 ReAct arXiv 论文，读了约一个小时。11:45 你剪贴了一段关于"推理步骤可解释性"的表述 —— 系统判断是核心概念（今日首次出现 + 你主动圈选）。14:02 你在 Obsidian 里手写了一条整理笔记，**这是今天价值最高的输入**，不是引用而是你自己的话。

> 证据：[[Events/2026-04-20/1145-react-explainability]] · [[Events/2026-04-20/1402-react-核心思想整理]]

### Q1 计划会议 · 50 分钟（14:30–15:20）

Zoom 会议 + 14:52 从 Mail 剪贴了一段资源重分配纪要。与你近期其他工作无关联，一次性输入。

> 证据：[[Events/2026-04-20/1452-q1-资源重分配]]

### VSCode 补全调研 · 36 分钟（晚间）

20:12 编辑 `continue.dev` 配置文件，随后在 Safari / GitHub / Reddit 之间来回。20:34 剪贴了一段"替代方案对比"。**没形成结论**。

> 证据：[[Events/2026-04-20/2034-continue-dev-alternatives]]

## 💡 需要你决定

**1. 「ReAct / Agent 范式」要不要立为长期主题？**

今天 5 条证据都指向它。上周也有 3 条相关但分散的记录（[[Topics/待定/agent-推理]]）。**建议升级** —— 证据够密，且你今天出现了"自己整理"的行为（显式兴趣信号）。

```
keypulse pipeline feedback promote react-agent
```

**2. VSCode 补全这件事今晚没结束**

只剪贴了对比，没有决策。**建议**：明早从 [[Events/2026-04-20/2034-continue-dev-alternatives]] 接着看，或放到 `#maybe-later` 归档。

```
keypulse pipeline feedback defer vscode-autocomplete
```

## 📌 附录

<details>
<summary>完整事件 8 条 · 过滤 12 条</summary>

| 时间 | 应用 | 内容 | 标记 |
|---|---|---|---|
| 10:23 | Safari | ReAct 论文 arXiv 首页 | 来源切换 |
| 11:45 | Safari | 剪贴：推理步骤可解释性 | 🔥 高分 · 今日首次 |
| 14:02 | Obsidian | 手动保存：ReAct 核心思想整理 | 🔥 高分 · 你主动写的 |
| 14:30 | Zoom | Q1 工程计划会议 | 会话 50m |
| 14:52 | Mail | 剪贴：Q1 资源重分配纪要 | 🔥 高分 · 决策信号 |
| 20:12 | VSCode | continue.dev 配置文件编辑 | 工作块 |
| 20:34 | Safari | 剪贴：VSCode 补全替代方案 | 💭 待决 |
| 21:08 | Terminal | git commit | metadata-only |

过滤：空闲 6 · 低密度碎片 4 · 低信号窗口 2

</details>

## 🔗 关联主题
[[Topics/AI 论文研究]] · [[Topics/待定/vscode-autocomplete]]
```

### 6.3 对比评估

| 维度 | Before | After |
|---|---|---|
| 读完能回忆今天做了什么 | 不能 | 能 |
| 知道下一步该做什么 | 不知道 | 知道（2 条决策 + 命令） |
| 视角 | 系统报告用户 | 用户自己回顾 |
| 打分细节 | 暴露 | 翻译成人话 |
| 时间信息 | 一个日期 | 时间块 + 时长 + 占比 |
| 历史连接 | 无 | "上周 3 条分散记录" |
| 行动路径 | 每候选模板建议 | 仅决策项给命令 |
| 可溯源 | candidate list | Event 卡链接 |

---

## 7. 降级策略（LLM 不可用时）

LLM 关闭或调用失败时，叙述层退化为"规则版摘要"：

```
## 🎯 今日主线

### AI 论文研究 · 2h14m（占今天 42%）

工作块包含 5 条证据，主要在 Safari 和 Obsidian。
关键事件：
- 11:45 剪贴：推理步骤可解释性（今日首次）
- 14:02 手动整理：ReAct 核心思想（你主动写的）

近 7 日连续第 2 天出现该主题。
```

不如 LLM 版流畅，但信息量相当、可读性仍显著优于现状。决策层同理走规则判定树，不依赖 LLM。

---

## 8. 落地改动清单

按实施顺序排列，分两批。

### 第一批（先看到效果，预计 1.5 天）

| # | 文件 | 改动 | 工作量 |
|---|---|---|---|
| 1 | `pipeline/surface.py` | 新增 `translate_why_selected(why_selected, metadata) -> list[str]` | 1h |
| 2 | `pipeline/` 新增 `narrative.py` | 实现 `aggregate_work_blocks(events, sessions) -> list[WorkBlock]` | 4h |
| 3 | `pipeline/write.py:64-71` | 替换 prompt，接入新的 `render_daily_narrative()` | 2h |
| 4 | `obsidian/exporter.py:175-231` | 重写 Dashboard 模板为四层结构（叙述层可为空时显示规则版） | 4h |
| 5 | `config.py:61` | `llm_mode` 默认从 `off` 改 `local-first` | 5min |

### 第二批（决策与闭环，预计 1 天）

| # | 文件 | 改动 | 工作量 |
|---|---|---|---|
| 6 | `pipeline/` 新增 `decisions.py` | 实现判定树，生成"需要你决定"列表 | 4h |
| 7 | `pipeline/aggregate.py` + `write.py` | 把 `read_feedback_events()` 结果注入 system prompt | 2h |
| 8 | `obsidian/exporter.py` | 在叙述层使用 `continuity` 字段加入跨日连接 | 2h |

### 不做的事（明确排除）

- 不重写 Event / Topic 卡模板（现有够用）
- 不引入新的前端组件（Obsidian 原生 markdown 足够）
- 不改数据库 schema（全部字段已存在）
- 不做自动 A/B 对比（反馈闭环先做"静态注入"即可）

---

## 9. 待拍板问题

以下三点定了才能写详细 brief 派 Codex / Sonnet 实施：

1. **叙述风格** —— 第二人称、带时间锚点、2-3 段，这个样例的风格 OK 吗？还是要更短更列表化 / 更长更详细？
2. **"需要你决定"的呈现形式** —— 给命令行（当前样例）vs 嵌 Obsidian callout 按钮（工程量 +0.5 天）
3. **LLM 后端** —— 本地 Ollama / 云端 OpenAI / 混合（隐私数据本地、摘要云端）。这决定 prompt 设计和成本预算

---

## 10. 风险与边界

| 风险 | 缓解 |
|---|---|
| LLM 生成幻觉（把没发生的事写进叙述） | Prompt 中明确"只能引用提供的 work_blocks，不得补充外部信息" + 所有引用强制带 Event 链接 |
| 高敏事件（sensitivity_level ≥ 2）被写进叙述 | 叙述 prompt 接收的数据先过脱敏层，只保留时间 + 应用 + "已记录但不展示内容"占位 |
| 决策过多导致疲劳 | §5.4 限制每日最多 3 条 |
| 反馈注入让 LLM 产生偏见（只写用户喜欢的） | 反馈注入作为"偏好上下文"而非"过滤指令"，叙述层仍覆盖所有工作块 |
| 跨日查询性能 | `continuity` 判断走索引查询（`raw_events(topic_key, ts_start)` 需加复合索引） |

---

## 11. 后续

本文档拍板后：
- 为第一批 5 个改动写详细 brief，派 Codex（在 worktree 隔离）实施
- 第一批落地后人工跑一天数据生成样例，对照本文档 §6.2 样例验收
- 第二批基于第一批的实际输出微调后再启动
