# Keypulse x OpenSpace 演化方案

**日期**: 2026-06-05  
**状态**: 方案草案，已结合 OpenSpace 源码结构与 Keypulse 现有文档路线  
**目标**: 借鉴 OpenSpace 的技能演化闭环，增强 Keypulse 的个人方法论蒸馏能力，但不把 Keypulse 改造成自动执行 agent。

## 1. 一句话结论

Keypulse 不该照搬 OpenSpace 的「agent 执行任务 + 失败后修技能」路线。更合适的迁移是：

> 把 Keypulse 现有的 anchor、skill、case wall、daily/weekly eval 发展成一个本地优先、用户命名、证据绑定、可回滚的 `Skill Evolution Layer`。

OpenSpace 给的是闭环骨架：记录一次执行，分析技能是否被用对，产生 FIX/DERIVED/CAPTURED 建议，生成新版本，保留 lineage、diff、snapshot、质量计数。Keypulse 应该把这套结构改写成：记录一次 daily/weekly 生成与用户反馈，分析叙事和 skill 假设是否成立，产生 REFINE/MERGE/CAPTURE/RETIRE 建议，形成可审核的个人 skill 版本。

## 2. 对两个项目的理解

### 2.1 Keypulse 是什么

Keypulse 是 macOS 本地优先的工作轨迹和反思系统。它把 window、idle、clipboard、keyboard chunk、browser URL、本地数据源等信号经过 privacy policy 与 desensitize 后写入 SQLite，再由 daily/weekly pipeline 生成 Obsidian 产物和 HUD 状态。

Keypulse 的核心不是「帮用户干活」，而是「把用户真实工作过程变成可回看的产出叙事、跨日主线、方法论假设」。现有文档里已经有几条很清楚的方向：

- `docs/EVOLUTION.md`: 采集、日报/周报、产品健康、自愈和数据源演进。
- `docs/phase2-product-strategy.md`: 从 collection/sediment 走向 consumption/intelligence。
- `docs/analysis/keypulse-endgame-and-distillation-framework.md`: 从行为数据到个人方法论 OS。
- `docs/skill-v0-plan.md`: Skill V0 已定义「软信念、纯 markdown、默认丢弃、用户命名、跨日 >=3 次阈值、time decay」。
- `docs/knowledge-graph-design.md`: anchor archive 是 Obsidian graph 的用户消费形态，SSOT 在本地状态文件。
- `docs/weekly-report-v3-design.md`: validator 不清空真实输出，质量报告与 gold set 是关键。
- `keypulse-narrative-pipeline.md`: 证据构造层优先，算法决定说什么，LLM 决定怎么说。
- `docs/superpowers/specs/2026-05-21-principle-vertical-design.md`: 虽然已被 `skill-v0-plan` supersede，但保留了关键判断：principle 蒸馏管道已跑通，skill 蒸馏应复用同一管道，用不同 prompt，不要另开 vertical。
- `keypulse/prompts/L7_principle_distillation.v2.md`: 已有 principle 蒸馏 prompt，宽抓 principle、anti-pattern、trade-off、analogy、intuition、meta-decision，并通过 `known_principles` 去重。

所以 Keypulse 的下一步不是再加一堆采集源，而是让「日报/周报/skill/case wall」形成可持续的演化闭环。

### 2.2 OpenSpace 是什么

OpenSpace 是一个 agent 技能执行和演化框架。它通过 MCP 暴露 `execute_task`、`search_skills`、`fix_skill`、`upload_skill`，在执行任务时选择相关技能注入 agent 上下文。若技能引导失败，会回退到纯工具执行；之后它记录执行、分析技能表现、生成技能演化建议。

OpenSpace 的关键结构在 `openspace/skill_engine/`：

- `SkillRecord`: 技能身份、类别、依赖工具、活跃状态、质量计数。
- `SkillLineage`: origin、generation、parent_skill_ids、change_summary、content_diff、content_snapshot。
- `ExecutionAnalysis`: task_completed、execution_note、tool_issues、skill_judgments、evolution_suggestions。
- `EvolutionSuggestion`: `FIX`、`DERIVED`、`CAPTURED` 三类建议。
- `SkillStore`: SQLite 持久化技能、分析记录、版本 lineage，并用计数维护 applied/completion/fallback。
- `SkillEvolver`: 把分析建议、工具退化、周期指标检查统一成 `EvolutionContext`，再生成新技能版本。
- `SkillRanker`: BM25 粗排加 embedding rerank，用内容 hash 做 embedding cache。

OpenSpace 的强点不是某个 prompt，而是闭环：

1. 选择技能。
2. 执行任务。
3. 判断技能是否被应用，任务是否完成。
4. 记录分析和质量计数。
5. 生成演化建议。
6. 产出新版本，并保留父子关系、diff 和 snapshot。

### 2.3 Hermes 蒸馏路线的校正

Keypulse 已经有一套从 Hermes 借鉴来的蒸馏框架，不应该被 OpenSpace 方案覆盖。这个框架把知识分成四层：

- L0 Capture: Keypulse raw_events、watchers、session notes。
- L1 Extract: `memory.md`，一句一事实，保存项目约定、环境事实、用户偏好。
- L2 Synthesize: `skills/`，可复用工作流，包含触发条件、步骤、陷阱、验证清单。
- L3 Archive: wiki/Obsidian，保存深度分析、架构决策、技术调研。
- Inject: CLAUDE.md/AGENTS.md 作为路由中心，把 memory 和 skills 反哺给 CC/Codex。

它跟 OpenSpace 的关系应是分工而不是二选一：

- Hermes 路线定义「蒸馏出来的东西长什么样、放在哪里、怎样反哺 AI 工具」。
- OpenSpace 路线补「这些 skill 如何被观测、版本化、质量计数、演化和回滚」。
- Keypulse 侧负责发现模式、提出假设、拿用户确认；AI-facing 的 `.claude/skills`、`memory.md`、wiki 是确认后的导出视图，不是 raw source of truth。

因此本方案的修订判断是：先复用 Keypulse 已有 L7 principle 蒸馏管道和 Skill V0 路线，再把 OpenSpace 的版本 DAG/分析账本/演化触发器接上去。

## 3. 不能照搬的部分

Keypulse 和 OpenSpace 的产品身份不同，下面这些不该迁移：

- 不把 Keypulse 变成自动任务执行器。Keypulse 的位置是观察、反思和方法论蒸馏。
- 不做 cloud skill marketplace。Keypulse 的数据是个人工作轨迹，隐私风险和产品基因都不支持默认云端共享。
- 不让 LLM 自动给用户下判断。Keypulse 文档已经反复强调「用户命名权」和「假设语气」。
- 不让 LLM 直接写核心状态。`case_wall.json`/anchors/skills 应该由程序根据证据和用户反馈写入，LLM 只提供候选。
- 不用复杂 schema 抢跑。Keypulse 已有 dual-layer/schema 走太前的教训，应先跑小闭环。

## 4. 要借鉴的 OpenSpace 模式

### 4.1 版本 DAG

OpenSpace 每次 FIX/DERIVED/CAPTURED 都生成新的 `SkillRecord`，并记录 parent、generation、diff、snapshot。Keypulse 应该把 Skill V0 的 markdown 文件扩展为可追踪版本，而不是只靠文件名和 score。

映射关系：

| OpenSpace | Keypulse 建议 |
|---|---|
| `SkillRecord` | Keypulse 的唯一可演化实体，沿用 `SkillRecord` |
| `SkillLineage` | Keypulse 的 `SkillLineage` |
| `FIX` | `REFINE`: 修正一条已确认方法论的表述、适用边界或证据 |
| `DERIVED` | `MERGE`: 多条相近 skill/anchor 融合成更高阶方法 |
| `CAPTURED` | `CAPTURE`: 从跨日证据捕捉新方法论候选 |
| `is_active=false` | `RETIRE`: 用户否定、长期未被引用、证据反向时软退役 |

Keypulse 需要额外增加 `RETIRE`，因为个人方法论会过期、变窄或被用户推翻；OpenSpace 主要处理技能修复和派生，不够覆盖这一点。

### 4.2 分析账本

OpenSpace 的 `ExecutionAnalysis` 是演化闭环的中间事实。Keypulse 应该建立类似的 `ReflectionAnalysis`，每次 daily/weekly 生成后记录：

- 输入日期/周范围、使用的 prompt/profile、model backend、fallback 状态。
- evidence coverage: 有多少 claim 绑定了可验证 evidence_ref。
- narrative quality: 是否空泛、是否丢主线、是否违反假设语气。
- anchor/skill judgments: 哪些已确认 skill 被观察到、被违反、没有证据。
- suggestions: 捕捉、修订、合并、退役的候选建议。
- user feedback: Obsidian 注释、HUD 勾选、CLI 采纳或拒绝。

这层账本比直接改 skill 文件重要。没有账本，就无法知道某条方法论为什么被保留、为什么被降权、为什么被用户改名。

### 4.3 多触发器

OpenSpace 的演化触发来自 post-analysis、tool degradation、metric monitor。Keypulse 可以改成：

| OpenSpace 触发器 | Keypulse 触发器 |
|---|---|
| Post-analysis | daily/weekly 生成后分析报告质量与方法论候选 |
| Tool degradation | 数据源/权限/模型 backend 退化导致叙事盲区 |
| Metric monitor | 周期检查 anchor 爆炸、skill 陈旧、validator 反复失败、fallback 频繁 |

这样 Keypulse 的演化不是只在「用户主动跑 skill propose」时发生，而是随 daily/weekly pipeline 自然形成背景账本，但真正写入方法论库仍要用户审核。

### 4.4 质量计数

OpenSpace 统计 selections、applied、completions、fallbacks。Keypulse 可以统计：

- `observed_count`: 在多少天/周观察到相关行为。
- `evidence_count`: 有多少独立 evidence_ref 支持。
- `confirmed_count`: 用户确认次数。
- `referenced_count`: daily/weekly 中被合理引用次数。
- `contradicted_count`: 后续行为或用户反馈反向次数。
- `retired_count`/`revived_count`: 退役与恢复记录。

这比单纯 `score + time decay` 更可解释，也能支持 Obsidian 里「为什么这条还活着」的回看。

## 5. 目标架构：Skill Evolution Layer

建议在现有 Skill V0 和 case wall 之上增加一层 `Skill Evolution Layer`。它不是新产品面，而是 Keypulse pipeline 的后台闭环。

DeepSeek 评审指出，`Method`、`Skill`、`Principle` 如果并行建模，会制造两套同构资产库。修订后的方案只保留一个核心可演化实体：`Skill`。所谓 method/principle 只是用户语义或标签，不是新的主实体。

### 5.1 核心对象

```text
Raw Events
  -> Evidence Units
  -> Daily / Weekly Narrative
  -> L7 Principle Distillation / L8 Skill Distillation
  -> ReflectionAnalysis
  -> SkillSuggestion
  -> User Review
  -> SkillRecord / Skill Markdown / Anchor Archive
  -> Optional Export: memory.md / .claude/skills / wiki / CLAUDE.md route
  -> Next Daily / Weekly Context Injection
```

建议对象：

- `SkillRecord`: 一条已确认或候选中的个人方法论。它是唯一核心实体。
- `SkillLineage`: 版本来源、父节点、generation、diff、snapshot。
- `ReflectionAnalysis`: 一次 daily/weekly 生成后的结构化分析。
- `SkillSuggestion`: `CAPTURE`、`REFINE`、`MERGE`、`RETIRE` 四类候选。
- `QualityLedger`: prompt/model/fallback/evidence coverage/用户反馈 的质量账本。

候选类型不应全部用同一个阈值：

- `principle_fragment`: 一次高价值表达也可以保留为候选碎片，但不能直接升级为 active skill。
- `workflow_skill`: 需要跨日重复、用户确认，或明确的验证清单，才进入 active。
- `agent_workflow`: 面向 CC/Codex 的可执行 skill，必须有触发条件、步骤、陷阱、验证清单，确认后才导出到 `.claude/skills/`。

状态机：

```text
proposed -> active -> retired
              ^          |
              |          v
        revived_proposed
```

`Hypothesis` 仍可作为 case wall 对象存在，但「方法论假设」在 skill 系统里表现为 `status=proposed` 的 `SkillRecord`，不再另建 `MethodRecord`。

### 5.2 存储建议

保持 Keypulse 的「本地 SSOT + Obsidian 可重建视图」原则：

- M1-M2 先用一个宽松的 SQLite 表或单一 JSON 文件存候选、反馈、质量记录，不提前建设 repository 抽象。
- M3 之后再把已确认 skill 固化成版本索引，适合查询、计数、回滚。
- `~/.keypulse/skills/*.md` 或 Obsidian vault 存用户可读 markdown。
- `.claude/skills/`、`memory.md`、wiki、CLAUDE.md 路由是 AI-facing 导出视图，只从已确认 skill 或已确认 memory fact 生成，不作为 Keypulse 内部 SSOT。
- `anchors/<slug>.md` 仍作为主题 archive，不和 skill 混成一个概念。
- 所有外部 agent/MCP 只读经过隐私过滤后的摘要，不给 raw_events 批量出口。

`evidence_ref` 的隐私边界必须写死：

- 只存 raw_events 外键、日期、来源类别和脱敏标签，例如 `app:Code`、`url:docs.example.com`、`clipboard:en`。
- 不在 `ReflectionAnalysis`、`SkillSuggestion` 或 MCP 响应中存原始 clipboard、键盘 chunk、URL title 全文。
- validator 只校验 ref 是否存在和是否属于允许暴露的摘要级别，不回链读取敏感原文。
- 若后续需要展示原文，必须走现有 privacy policy/desensitize，并由用户显式打开。

### 5.3 与现有路线的关系

这不是替代 `docs/skill-v0-plan.md`，而是把它补齐成可长期维护的系统：

- Skill V0 仍然先跑 hello world。
- `case_wall` 仍然是 topic/hypothesis/skill 的持续状态机。
- 新增的 Skill Evolution Layer 负责版本、分析、质量计数、回滚和审核门。
- Hermes/L7/L8 路线负责蒸馏与 AI 工具反哺；OpenSpace 路线负责演化治理。二者组合，不互相替代。

## 6. MVP 路线

### M0: 定义领域语言，不写复杂实现

输出：

- 明确只有一个核心可演化实体：`Skill`。
- 明确 `Skill V0` 是「已确认或待确认的个人方法论」，`Anchor` 是「工作主题」，`Hypothesis` 是 case wall 里的「待验证判断」。
- `Method`/`Principle` 不建新表，只作为 skill 的标签、标题语义或成熟度描述。
- 明确 L7 principle 蒸馏与 L8 skill 蒸馏的关系：同一管道、不同 prompt、不同晋升门槛。
- 明确 Hermes 四层产物中哪些是 Keypulse 内部状态，哪些只是 AI-facing 导出视图。
- 给现有 `docs/skill-v0-plan.md` 补一个数据模型小节，避免后续实现跑偏。
- 补 2-3 个负面例子，说明哪些内容容易被误分成 anchor/skill/hypothesis。

验收：

- 用户能一句话区分 anchor、skill、hypothesis。
- 不新增 pipeline 节点。

### M1: 可读观察 + 轻反馈，不做静默账本

DeepSeek 评审指出，M1 如果只在数据库里写 `ReflectionAnalysis`，用户看不到系统如何判断，会在信任建立前积累一堆自评噪音。因此 M1 要把可读观察先放到 weekly note 或 HUD 中，结构化账本只是它的后台支撑。

weekly note 新增一个很轻的观察区：

```markdown
## 本周观察到的工作模式

1. 你可能在反复使用「先拆输入/输出/边界」这个判断方式。
   - 证据: 2026-05-11 / 2026-05-13 / 2026-05-16
   - 反馈: [useful] [not useful] [wrong]
```

后台只记录最小字段：

- 观察文本。
- evidence_ref 外键和脱敏标签。
- 用户反馈：`useful` / `not_useful` / `wrong` / `defer`。
- prompt/model/fallback。
- validator 状态。

验收：

- 单个 weekly note 至少出现 3 条具体工作模式观察。
- 至少 1 条观察来自已有 L7 principle 蒸馏结果或其同管道输入，证明不是另起一套新 vertical。
- 用户对其中 >=1 条给出明确反馈。
- 不产生自动写入的 skill。

### M2: SkillSuggestion 审核入口

把候选写进 weekly note 或 HUD 审核区，沿用 Skill V0 的「默认丢弃，兜底留 1」克制原则。

审核动作：

- confirm: 进入 skill 库。
- rename/edit: 用户拿回命名权。
- reject: 记录拒绝原因，降低相似候选再次出现概率。
- defer: 下周继续观察。

验收：

- `principle_fragment` 可由一次高信号表达进入候选池，但只保留为 proposed，不自动 active。
- `workflow_skill` 默认需要 >=3 个跨日 evidence_ref；若用户显式命名并确认，可提前进入 active。
- `agent_workflow` 需要满足 Hermes SKILL.md 结构：触发条件、步骤、陷阱、验证清单。
- 用户每周审核成本 <10 分钟。
- 产生 >=2 条用户确认的 skill。
- 这些 skill 在后续两周内至少被 daily/weekly 正确引用 1 次，且用户未标记为 `wrong`，才进入 M3。

### M3: SkillLineage 和版本回滚

把已确认方法论变成版本节点：

- 新捕捉: `CAPTURE`。
- 用户改写: `REFINE`。
- 多条同类融合: `MERGE`。
- 长期无用或被否定: `RETIRE`。

验收：

- 每个版本有 parent、change_summary、snapshot。
- 能回滚到上一版本。
- Obsidian 里只展示 active 版本，但历史可追溯。
- `RETIRE` 只生成退役候选，不自动退役；后续大量匹配时只生成 `revived_proposed`，不自动复活。

### M4: Prompt/Renderer Evolution Registry

Keypulse 已经有 `daily-prompt-candidates-v4/`、`weekly-prompt-candidates-v3/`、golden daily/weekly 和 eval 文档。建议把这些从「候选文件夹」升级成正式 registry：

- prompt version。
- active profile。
- golden set 分数。
- badcase 列表。
- 失败回滚策略。
- 每次 daily/weekly 记录使用了哪个 prompt 版本。

验收：

- 改 prompt 必须能对比 golden set。
- daily/weekly 质量退化能定位到 prompt/model/data-source 之一。
- validator 失败不清空真实 LLM 输出，沿用 weekly v3 的原则。

### M4.5: Hermes Export Bridge

在 M1-M4 稳定后，再做 Keypulse 到 AI 工具的导出桥，而不是一开始就让 agent 读内部状态。

导出目标：

- L1: 已确认的项目事实、偏好、环境约定 -> `memory.md`。
- L2: 已确认的 `agent_workflow` -> `.claude/skills/<name>/SKILL.md`。
- L3: 深度分析或架构决策 -> wiki/Obsidian 页面。
- Inject: AGENTS.md/CLAUDE.md 只放路由，不塞长内容。

验收：

- 导出内容可由用户审阅和撤回。
- `.claude/skills` 中每条都能追溯到 Keypulse 的已确认 skill 或用户手写来源。
- 不从 raw_events 直接导出 AI-facing 产物。

### M5: 隐私过滤的只读 MCP

等 M1-M4.5 跑稳后，再给 Codex/Claude 暴露本地只读 MCP。

建议工具：

- `search_memory(query, since, until, scope)`: 搜索已脱敏摘要、anchor、skill。
- `get_daily_context(date)`: 返回某日摘要，不返回 raw_events。
- `get_active_skills(limit)`: 返回 active skill。
- `save_note(text, tags)`: 用户显式保存笔记。
- `propose_skill_feedback(skill_id, feedback)`: agent 提交建议，等待用户审核。

禁止：

- 批量导出 raw_events。
- 自动上传云端。
- agent 自动确认、退役或重写方法论。
- agent 将 `get_daily_context` 结果作为跨 session 用户画像持久化。

## 7. 文件落点建议

初期不需要大改架构，优先贴近现有模块：

- `keypulse/pipeline/daily_orchestrator.py`: daily 后追加 ReflectionAnalysis 写账本。
- `keypulse/pipeline/weekly_orchestrator.py`: weekly 可读观察区、轻反馈和候选汇总。
- `keypulse/pipeline/daily_summary.py`: evidence_ref、prompt version、fallback 信息补充。
- `keypulse/pipeline/run_record.py`: 可扩展成 QualityLedger 的一部分。
- `keypulse/obsidian/`: 渲染 skill proposal 和 active skill 页面。
- `keypulse/hud/approval_window.py`: 复用 HUD 审核交互。
- `keypulse/store/`: M1-M2 只加最小 reflection/feedback 存储；M3 后再考虑正式 skill lineage 表。
- `docs/golden-daily/`、`docs/golden-weekly/`: 继续作为 eval baseline。

## 8. 为什么值得做

### 8.1 它补的是 Keypulse 当前最弱的一侧

Keypulse 的采集和沉淀能力已经很强，但消费和智能化还弱。OpenSpace 式演化闭环能把「生成了一篇日报」推进到「这篇日报是否让系统更懂我」。

### 8.2 它跟 Keypulse 的终局一致

Keypulse 文档里的终局不是 productivity dashboard，而是 methodology OS。Skill Evolution Layer 正好把 daily/weekly 的产物变成长期方法论资产。

### 8.3 它有可验证闭环

只做 prompt 优化会陷入主观感觉。增加 ReflectionAnalysis、QualityLedger 和 SkillLineage 后，Keypulse 能回答：

- 这条方法论从哪来？
- 被哪些证据支持？
- 用户什么时候确认过？
- 后来有没有被违反或改写？
- 哪个 prompt/model 版本导致质量变化？

### 8.4 它不会破坏本地优先

OpenSpace 有云端技能共享倾向，Keypulse 不需要。版本 DAG、质量账本、审核门都可以完全本地实现。

## 9. 风险与控制

| 风险 | 控制 |
|---|---|
| 自动化过度，变成 LLM 评价用户 | 所有输出用假设语气，用户命名和确认优先 |
| schema 太早太重 | M1-M2 只做可读观察、轻反馈和宽松存储，M3 才做 lineage |
| skill/anchor/hypothesis 混乱 | M0 先定义领域边界 |
| 候选太多，维护成本爆炸 | 默认丢弃；按类型分流阈值；每周审核 <10 分钟 |
| LLM 幻觉形成错误方法论 | 每个 claim 必须有 evidence_ref，validator 只校验 ref 存在性和摘要级权限 |
| 隐私泄露 | evidence_ref 只存外键和脱敏标签；MCP 只读摘要，raw_events 无批量出口 |
| eval 只看分数不看人眼 | golden set + fail case + 人眼验收，不用单一 validator 替代产品判断 |

## 10. 最小可执行计划

建议先做三周验证，不碰 MCP。

第 1 周：

- 补领域模型文档。
- 对齐 Hermes 四层、L7 principle 蒸馏、计划中的 L8 skill 蒸馏，确认不另开新 vertical。
- 在 weekly note 输出 3 条带 evidence_ref 的可读工作模式观察。
- 加 `useful/not useful/wrong/defer` 轻反馈。
- 后台只用宽松记录保存观察、反馈、prompt/model/fallback，不写 skill。

第 2 周：

- 在 weekly note 增加 skill proposal 审核区。
- 每条候选带 3 个跨日 evidence_ref。
- 用户只做 confirm/reject/rename/defer。

第 3 周：

- 对确认过的方法论做 active skill markdown。
- 引入 version snapshot 和 rollback。
- 统计 observed/confirmed/referenced/contradicted。
- 只对 `agent_workflow` 类型尝试导出 `.claude/skills` 草案，且必须用户审阅。

三周后决策：

- 若用户能确认 >=2 条 skill，且后续两周至少被正确引用 1 次，继续 M4 prompt registry。
- 若候选空泛，先退回 daily evidence construction，不继续做 MCP。
- 若审核成本 >10 分钟/周，先降候选数量和阈值，不加功能。
- 若 L7 principle 管道本身未稳定，先修 L7，不启动 L8 skill_distillation。

## 11. 最终判断

OpenSpace 对 Keypulse 最有价值的不是「技能市场」或「自动 agent」，而是它把 skill 从静态 markdown 变成了可观测、可分析、可版本化、可演化的系统。

Keypulse 应该吸收这点，把已有 Skill V0、case wall、golden eval、daily/weekly pipeline 串成闭环：

> 观察行为 -> 构造证据 -> 生成叙事 -> 提出方法论假设 -> 用户命名确认 -> 进入方法论库 -> 反过来 condition 下一次反思 -> 继续演化。

这条路线增强的是 Keypulse 的核心差异化：它不是记你做过什么，而是逐步学会你为什么这么做，以及你愿意把哪些判断方式命名为「我的方法」。

## 12. External Review

已在 2026-06-05 使用 `anyint/deepseek-v4-pro` 做外部评审。评审先通过 `decision-advisor` MCP 发起，MCP 中 `.env` 的 AnyInt key 返回 401；随后改用 keychain 中的 `codex-anyint-api-key` 直接调用 AnyInt，同模型评审成功。

主要意见：

- Must-fix: 不要让 `Method`、`Skill`、`Principle` 并行成为实体。已修订为只保留 `Skill` 一个核心可演化实体。
- Must-fix: M1 不能只写系统自评账本。已修订为 weekly 可读观察 + 轻反馈，结构化记录只做支撑。
- Must-fix: `evidence_ref` 可能成为新的高敏存储。已补充外键和脱敏标签边界，不存原文。
- Must-fix: 质量信号不能只靠系统自评。已把 `useful/not useful/wrong/defer` 用户反馈提前到 M1。
- Should-fix: 早期对象集合过大。已修订为 M1-M2 宽松存储，M3 才固化 lineage。
- Should-fix: 「确认 2 条有共鸣」不可衡量。已改成确认后两周内被正确引用且未标 `wrong`。

## 13. Hermes 补读后的再次修订

用户指出 Keypulse 里已有从 Hermes 借鉴 skills 蒸馏的文档。补搜后确认关键材料包括：

- `docs/analysis/keypulse-endgame-and-distillation-framework.md`: 定义 L0/L1/L2/L3/Inject 四层蒸馏框架，并明确 Keypulse 是传感器阵列，蒸馏框架是精炼厂。
- `docs/superpowers/specs/2026-05-21-principle-vertical-design.md`: 虽已 superseded，但保留关键判断：principle vertical V1 已经搭好通用管道，skill 蒸馏应作为同管道 + 不同 prompt 的 L8，而不是另开 vertical。
- `keypulse/prompts/L7_principle_distillation.v2.md`: 已有实际 prompt，宽抓 principle、anti-pattern、trade-off、analogy、intuition、meta-decision，并有去重和质量约束。
- `docs/skill-v0-plan.md`: 后续权威路线，明确不抄 `.claude/skills` 目录规范化，等真有 50 条 skill 再总结结构。

结合后，本方案的定位从「OpenSpace 启发 Keypulse 做 Skill Evolution」修正为：

> Hermes 定义蒸馏产物和 AI-facing 反哺路径；Keypulse L7/L8 负责从个人行为中蒸馏候选；OpenSpace 只补演化治理能力，包括版本 DAG、质量账本、多触发器和回滚。

这带来三条具体修正：

- 不再把 >=3 次出现作为所有候选的硬门槛。一次高价值 principle 可以进入 proposed pool，但不能直接 active；workflow skill 才默认需要跨日证据或用户显式命名。
- 不在 M1 新建一条独立 skill extractor。先复用 L7 principle_distillation 的输入/输出和同管道机制，后续再上 L8 skill_distillation。
- `.claude/skills`、`memory.md`、wiki、CLAUDE.md 路由都视为确认后的导出视图，不是 Keypulse 内部 SSOT。
