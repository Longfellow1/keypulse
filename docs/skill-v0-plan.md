# Skill V0 — 个人方法论蒸馏 Plan

- **分支（待开）**: `feat/skill-v0`
- **日期**: 2026-05-10
- **状态**: 设计阶段，待 P0（weekly M5 内容修 + daily prompt 升级）完成后启动
- **前置依赖**: 日报地基质量稳住（高质量 daily 是 skill 抽取的输入）

---

## 1. 背景 — 为什么开这个文档

完成 daily renderer 重构（PR #5/#6）后，KeyPulse 走到下一个岔路口：**继续做日报/周报生成器**，还是**升维成个人方法论蒸馏系统**。

讨论结论：日报/周报是观察层，**skill 库是沉淀层**。两者闭环 —— KeyPulse 提假设 → 用户挑/丢 → skill 反过来 condition 下次分析。这层闭环是 Notion / Obsidian / RescueTime 都做不到的部分，是 KeyPulse 真正的差异化。

> 没有这一层，KeyPulse 退化为日报生成器，产品价值不足以支撑长期投入。

参考愿景：`docs/analysis/keypulse-endgame-and-distillation-framework.md`。本文档不重述愿景，只规划"从今天到 hello world 跑通"这一段。

---

## 2. Skill 形态 — 三定律

| 定律 | 设计 | 产品意图 |
|---|---|---|
| **1. 软信念，纯 markdown** | skill 是"我观察到 / 我相信 / 我会怎么做"的个人信念体，纯文本 | 文本可移植 → 用户真正"拥有"，扔给任何 LLM agent 都能用。不绑定 KeyPulse |
| **2. 渐进披露 + 默认丢弃 + 兜底留 1** | KeyPulse 提 N 个候选，每个 1 句描述。用户勾选保留，不勾的全丢。全不勾时只留分数最高的 1 条 | 克制是核心特性。每次只让用户做一次轻判断，避免 PKM 工具的"建得越来越多最后没人维护"腐烂 |
| **3. 双消费** | 用户自取（Obsidian 看回） + KeyPulse LLM condition 后续 daily/weekly 分析 | 闭环。用户拿走是他的事，但 KeyPulse 也要用起来才能让分析变贴 |

---

## 3. 演化机制 — 冲突 / 撤销

**自动以新的为核心**。库里每条 skill 配 score，**时间越久分数越低**（time decay）。同类同源的两条 skill 不会同时在 prompt 里冲突存在 —— 旧条衰减下来低于阈值就自然出局。

设计要点：
- 入库时给 score = 100
- 衰减函数：每周 -X 分（具体曲线 v0 写死，hello world 跑过再调）
- 用户每次确认引用某条 skill → score +Y（被验证的方法论延寿）
- 取入 prompt 时按 score 排序，取 top N

不做：手动撤销 UI、版本管理、merge 工具。**让时间做仲裁**。

---

## 4. 触发与披露

| 维度 | 设计 |
|---|---|
| **频率** | 每周一次（与 weekly 联动），用户也可手动 `keypulse skill propose` 强触发 |
| **披露形态** | N 个候选 + 每个 1 句描述 + score。终端勾选界面 |
| **兜底** | 用户全不勾，仍保留 score 最高的 1 条（避免库永远不长大的死锁） |
| **配图** | v1: Mermaid + emoji（纯文本，Obsidian 原生渲染）。v2 实验 Unsplash API 关键词配图（待评估版权 + key 管理）。**不走 OpenAI 图片 API**（违反"永不 SaaS / 本地优先"基因） |

---

## 5. Hello World — 一次性脚本验证闭环

**目标**：跑一次完整生命周期，看产品假设是不是真的成立。**不进 daemon、不进 schema、不进 cron**。

### 输入
P0.5 跑出的 5/11-5/17 一周新格式 daily（高质量 baseline 是必要前提）。

### 命令
```bash
keypulse skill propose --since 2026-05-11 --until 2026-05-17
```

### 输出（终端）
```
我读了一周日报，发现以下可能成形的工作方法。勾选保留：

[ ] 1. 拿到需求先拆 输入/输出/边界                  (score: 87)
[ ] 2. 改动 >1 文件先说方案再动手                   (score: 82)
[ ] 3. PR review 时优先看接线点不看实现细节         (score: 76)
[ ] 4. 跑测试前先用真实数据手验前/后差异            (score: 71)
[ ] 5. 子 agent 报"改了"必须 git diff 自验          (score: 68)

回车确认。不勾选则只保留 #1，其余丢弃。
```

### 落盘
`~/.keypulse/skills/{slug}.md`：

```markdown
---
name: 拿到需求先拆 输入/输出/边界
confirmed_at: 2026-05-17
score: 100
source: derived from 2026-05-11..2026-05-17
---

## 我观察到
你这周三次开新任务时第一件事都是拆"输入是什么、输出是什么、边界在哪"。
不拆的那一次走了弯路。

## 我相信
拆解一定要赶在动手前完成。后补的拆解几乎没用，因为已经被实现路径绑死了。

## 我会怎么做
新任务到手，30 秒内先写三行：输入 / 输出 / 边界。写不出来说明任务还没想清楚，
不要进编码阶段。
```

### 闭环 — 下次 daily 加载 skill 上下文
下一次跑 daily（5/18）时，prompt 注入：

```
这是该用户已确认的工作方法（按 score 倒序，取 top N）：

{skills_text}

请在今天的复盘中，**仅当观察到相关行为时**引用这些方法，不要为了引用而引用。
```

---

## 6. Gold Set 标准

skill propose 的 prompt **必须走 Gold Set 流程**（见 `CLAUDE.md` "输出质量标准 — Gold Set" 章节）：

1. Opus 4.7 起 2-3 版候选 prompt（不同视角 / 详略 / 个性化语气）
2. 人工对照真实数据微调
3. 固化为 `docs/golden-skill/` 下的 baseline 文件
4. 后续改动跟 baseline 比退化

不允许"一版定稿就上线 skill propose"。

---

## 7. 串行节奏（不可并行）

```
P0    weekly M5 内容修 + daily prompt 同步升级
       │  目的：日报/周报内容质量稳住，新格式
       ▼
P0.5  跑 5/11-5/17 一周新格式 daily baseline
       │  目的：给 skill 抽取喂高质量输入
       ▼
P1    skill v0 hello world 实现（~300 行代码）
       │  目的：跑通完整生命周期
       ▼
P1 验收 → P2 决策点
```

**为什么必须串行**：skill 候选的质量取决于 daily 输入的质量。输入流水话 → 候选必然空泛 → 用户全不勾 → 实验得到"假阴性"结论，把对的方向给毙了。

---

## 8. 验收标准（P1 → P2 决策门）

两条**都满足**才进 P2：

1. **用户主观勾选率**：5 个候选 skill，用户至少勾 2 条，且勾的时候有"对，这就是我的"感受
2. **闭环可感知**：注入 skill 上下文后的 daily 输出 vs 不注入的，用户能主观感知到"更懂他"

任一不达标 → 砍掉重想，不进 P2。

不设自动指标。**这是产品判断不是工程指标**，validator 跑不出来。

---

## 9. 暂缓清单（明确不做）

| 事项 | 何时考虑 |
|---|---|
| `.claude/skills/` 目录规范化抄 Hermes 四层（L0/L1/L2/L3） | 永远不抄结构。等真有 50 条 skill 再总结结构 |
| skill 商店 / 市场 / 分享机制 | 等真有用户在用 skill 一年以上 |
| Pro / Teams 商业化设计 | 0→1 阶段不讨论 |
| 跨设备 skill 同步 | 等多设备用户出现 |
| skill 间关系图谱 / 依赖 | 等手动维护成本暴露 |
| skill 自动撤销 UI / 版本管理 | time decay 是兜底，不够再加 |

---

## 10. 未决问题（需要 hello world 跑过再答）

1. **time decay 曲线**：每周 -X 分中 X 取多少？衰减阈值低于多少出局？v0 写死先跑
2. **skill 在 prompt 里塞多大体量**：top N 是多少？整段拼接还是摘要？token 预算多少？
3. **skill 命名（slug）**：LLM 自动生成还是用户拍？
4. **propose 与 weekly 触发的关系**：propose 跑在 weekly 之前还是之后？是否复用 weekly 的 prompt 数据？
5. **冷启动**：第一次跑（用户库为空时）skill 上下文段塞什么？纯 placeholder？

---

## 11. 进度记录

- [ ] **P0** — weekly M5 内容修 + daily prompt 升级
- [ ] **P0.5** — 跑 5/11-5/17 一周新格式 baseline
- [ ] **P1** — skill v0 hello world 实现 + gold set baseline 建立
- [ ] **P1 验收** — 主观勾选率 + 闭环可感知 双达标
- [ ] **P2 决策点** — 进 / 退 / 重想

---

## 12. 文档关系

| 文档 | 角色 |
|---|---|
| `docs/analysis/keypulse-endgame-and-distillation-framework.md` | **愿景备忘**（aspirational）。长期方向，不是路线图 |
| `docs/skill-v0-plan.md` | **本文档**。规划到 P1 验收为止，不延伸 |
| `docs/refactor-daily-renderer-unification.md` | 已完成的 daily renderer 重构记录 |
| `CLAUDE.md` "输出质量标准 — Gold Set" | 跨任务的 LLM 产出标准，skill propose 必须遵守 |

---

## 13. 案件墙（Case Wall）— 让 LLM 越写越出活的状态机

### 13.1 问题陈述

Skill v0 解决的是"已确认的方法论"沉淀。但还有更深的问题：**LLM 在 daily/weekly 里能不能像侦探一样，越写越出活、越写越锐？** 当前结构下答案是**不能**：

- Prompt 注入"昨天 daily 全文" → LLM 看不到模式，只看到一篇散文
- "跨日延续"段名连续 5 天空白（5/6-5/10 全部）→ 渲染层留位但无数据源
- 同一根因 5/8、5/9、5/10 三次"修复"，daily 没有任何一天指出"这是反复打补丁"
- 周报喂 daily，daily 不喂 daily → 时间越往上越聚合，越往下越孤立

要让 LLM"越写越出活"，靠的不是 prompt 变聪明，是**上下文越来越厚**。这层厚上下文不能是 LLM 自己写出来的散文（那就成了第二层套话），必须是**结构化的持续状态机** —— 案件墙。

### 13.2 三类对象 + 三类关系

| 对象 | 是什么 | 例子 |
|---|---|---|
| **Topic（主线/案件）** | 跨日存在的工作流，已有 H3 anchor 升格 | `m5-dual-layer`（5/4 起，7 天活跃，5/10 closed）|
| **Hypothesis（悬案）** | 用户提的或 LLM 推的待验证假设 | "同根因被反复修是工艺问题不是技术问题"（5/9 raised by user, status=open）|
| **Skill（信念）** | 已确认的方法论（同 §2-§5）| "拿到需求先拆 输入/输出/边界" |

| 关系 | 含义 |
|---|---|
| `continues` | 今天的 X 续了昨天的 Y |
| `resolves / reopens` | 今天事件关 / 重开某 hypothesis |
| `references` | 今天行为验证 / 违反某 skill |

### 13.3 物理形态

`~/.keypulse/state/case_wall.json` 是单一真源：

```jsonc
{
  "topics": {
    "m5-dual-layer": {
      "first_seen": "2026-05-04", "last_active": "2026-05-10",
      "days_active": 7, "status": "completed",
      "history": [
        { "date": "2026-05-09", "summary": "事故：6 renderer 拼", "evidence_ref": "events/2026-05-09/..." },
        { "date": "2026-05-10", "summary": "重构 6→1", "evidence_ref": "..." }
      ]
    }
  },
  "hypotheses": {
    "h-recurring-fix-pattern": {
      "raised_at": "2026-05-09", "raised_by": "user",
      "claim": "同根因反复修是工艺问题",
      "evidence_for": [
        { "date": "2026-05-09", "ref": "升格机制改 4 次" },
        { "date": "2026-05-10", "ref": "6 renderer 拼" }
      ],
      "status": "open"
    }
  }
}
```

### 13.4 幻觉防御（三层兜底，缺一不可）

1. **LLM 不写 case_wall.json，只读**。写入靠 KeyPulse 解析 daily.md 的 H3 anchor / events / user feedback。LLM 拿到的永远是过滤后的快照。
2. **每个 claim 必须带 evidence_ref**。Daily 里 LLM 说"这是这周第 3 次动 X"，prompt 强制旁标 ref。Validator 只校验 ref 是否真存在，不评 LLM 文字。Ref 假 → 整段重生成。
3. **用户每周一次 case_wall review**。周报触发时弹"墙上有 N 个 hypothesis 待你确认/驳回"，不是 LLM 自己沉淀。Skill v0 渐进披露同款模式。

### 13.5 环比 / 同比 — diff 状态不 diff 文字

| 时间维度 | 怎么算 |
|---|---|
| **昨日同比** | 昨天 vs 今天的 active topics 差集；昨天 hypothesis 今天有没有新 evidence |
| **跨周同比** | 上周快照 vs 本周；哪些 topic 跨周续命，哪些 hypothesis 何时关闭，days_active 趋势 |
| **多月环比** | 哪些 skill 被反复 reference，哪些被 reopens（已沉淀方法论又被违反 → 元信号）|

写出来不是"本周完成度+12%"垃圾环比，是"M5 主题已活跃 8 天，5/10 closed；新主题 case-wall 5/11 raised，预计同 m5 pattern 持续 5+ 天"这种侦探报告。

### 13.6 跟 skill v0 的关系

**Skill 是案件墙的"已结案档案"**。Skill v0 hello world = 案件墙只有 skill 一类对象的最简版。完整案件墙是 P2 之后的事。

### 13.7 落地路径（递进，不一次做完）

```
P0     daily prompt 升级（A/B/C 候选）
       └─ B 候选里"昨天 daily 的明日锚点段注入今天 prompt" = 最小 MVP
        ▼
P0.5   跑 5/11-5/17 baseline
        ▼
P1     skill v0 hello world（案件墙的"已结案"维度）
        ▼
P1.5   topic 维度入墙：扫 daily.md 历史 H3 anchor 自动建 topics.json
       Daily prompt 注入 active topics 历史
       验收：5 天后 daily 写出"M5 第 N 天" / "这次是 reopens 不是新增"
        ▼
P2     hypothesis 维度入墙：用户在 weekly review 提假设 → 入 hypotheses.json
       Daily 跑时 LLM 必须为每个 open hypothesis 报"今天有无新 evidence"
        ▼
P3     完整案件墙：三类对象 + 三类关系全跑通；跨日跨周环比从状态机出
```

**每加一个对象/字段都要先想"LLM 看到这个会不会被字段牵着走变僵化"**。先 topic.history 跑通，再加 hypothesis。

### 13.8 风险

1. **schema 走太前** — 之前 dual-layer schema 折腾过一轮，不能重蹈
2. **冷启动 5 天才有体感** — 周报是最快验证窗口（每周 1 次差分）
3. **跟 Obsidian 关系** — 墙是 KeyPulse 内部状态，不是用户消费品；周报里要把墙的关键信号渲染成人话
4. **用户不审核就废** — Hypothesis 永远 open 会污染 prompt，必须强制 weekly review 关一些

---

## 14. 待办 — Eval 系统建设（2026-05-12 起）

**触发事件**：2026-05-11 daily v2 上线后，validator 分数全过但人眼读出 6 个 H3 段被硬截断 → 现有 validator 是 proxy metric，不能代表产品验收。

**目标**：daily / weekly / skills 三条产物各自要有独立可跑的 eval 系统，作为交付门槛。

### 14.1 三套 eval 的核心差异

| 产物 | 输入 | 评估维度 | 黄金集形态 |
|---|---|---|---|
| **daily eval** | 一天事件 + 跨日上下文 → 1 篇 daily.md | H3 完整性 / 跨日延续命中 / 卡壳段命中 / 反模式不出现 / 客观事实保留率 | `docs/golden-daily/` 已有 5/6 / 5/9，需扩到 5-7 个不同形态日 |
| **weekly eval** | 5-7 天 daily + Q-tier 标签 → 1 份周报 | Q3 主线占比 / 套话密度 / 主线锚定准确率 / dual-style 差异度 | `docs/golden-weekly/` 已有 W19 双 style，下一轮 M5 修后重起 |
| **skill propose eval** | 多周 daily/weekly → N 个 skill 候选 | 候选独特性 / 与历史 skill 冲突检测 / 用户保留率拟合 | 待 hello world 跑通后建立 |

### 14.2 eval 系统的最低标准

- **不只跑分数，要跑 fail case**：每次出分要列具体哪几条 H3 / 哪几段重复 / 哪条主线丢失。
- **覆盖刚踩过的坑**：H3 段长度截断、空跨日段名、复读尾巴、流水开头——每个事故都要变成一条 check。
- **可以本地一行命令跑**：`keypulse eval daily --gold golden-daily/ --candidate ~/Go/Knowledge/Daily/2026-05-08.md`。
- **CI / 提交前可调用**：改 prompt → 跑 eval → 不达标不允许 merge。

### 14.3 实施顺序

1. **daily eval 先做**（已有 5/6 / 5/9 双 golden + 现成 validator 框架可改）
2. **weekly eval 跟上**（M5 内容修完起 baseline）
3. **skill eval 最后**（V0 hello world 跑通才有数据）

### 14.4 不要做

- 不上字数硬卡 / 死模板 / blacklist 砍 LLM 输出（违反 `feedback_no_dead_rules_on_llm.md`）
- 不上"句尾必须以 X 结束"这种 surface 规则
- eval 要打**语义层面的弱信号**（重复段、丢失段、客观事实丢失率），不打 surface 形式

### 14.5 同步待修 — daily H3 段截断真因

**事故根因（2026-05-11 已定位但未修对）**：
- `narrative_one_line` 字段设计为 120 字索引（契约对），但 daily renderer 把多个 cluster 的它拼接当 H3 段正文 → 多 cluster 主题段被硬切
- 已加 `_extract_h3_section(source_markdown, display)` 优先取 LLM 原文，但 display 匹配在多 cluster 场景下没命中（ragflow 单 cluster 命中，daily-report-quality-fix 多 cluster 没命中）
- LLM 原始 markdown 未持久化到 `~/.keypulse/daily-summary/*.json`，重渲只能重新调 LLM

**明天要做**：
1. 持久化 `narrative_markdown` 到 daily-summary cache（让 `keypulse daily run --replay <date>` 能不调 LLM 重渲）
2. 排查 `_extract_h3_section` 多 cluster 场景的 display 匹配——很可能 LLM 写的 H3 标题用了 cluster.display_name（如「周报设计与成功标准定义」），而 daily_summary 传的是 anchor.display（如「周报 v3 设计与落地」）
3. 修对后用 5/12 真实数据跑一遍，**人眼读完 daily.md** 才能算完成
4. 跑 5/11 重新生成（备份没有 5/11，目前只能空着）
5. trigger=23:30 增量逻辑要加文档提示 / CLI warning，避免再次踩坑

**5/10 LLM SSL EOF 失败**：上游网络问题，可能需要加重试间隔 / 切换 provider 兜底，但不是优先级。
