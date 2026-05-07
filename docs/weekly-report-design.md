# 周报功能设计 · 评审版

**首版**：2026-05-06
**最近更新**：2026-05-06(轮 2 — 合并 Harland 8 点反馈 + 4 项目调研)
**作者**：Harland + Claude (Opus)
**状态**：方案评审中,未开工
**关联**：`docs/phase2-product-strategy.md`
**说明**：本文档为周报二阶段唯一来源,无单独 PRD,所有变更增量更新到本文件

---

## 0 · TL;DR

- **Daily 出报时机**:18:00 首版 + 增量 + 23:30 收尾(章节范式 18:00 后锁定,只追加不重写)
- **Weekly 触发**:周五 23:35,≥5 天日报阈值,不达标走 HUD fallback 一行
- **Weekly 结构**:在 weekly.md 顺序排列两个板块 —— `## 这周的主线`(客观记录)+ `## 这周的回声`(探索者),不折叠
- **聚类是核心算法**,daily/weekly 共用一套(daily 一次,weekly 只做 reconcile)
- **聚类按"事儿/主题"主导,时间辅助** —— 同一件事跨 12 小时仍归一类
- **方案 C**:聚类 + 重写合并一次 LLM 调用(daily 一天 ≤ 30 事件,token 量小,装得下)
- **数据架构重构**:Events / Topics 迁到 `.keypulse/` 隐藏目录,用户 vault 里只见 Daily / Weekly
- **新增基础设施**:`.keypulse/hot.md`(滑窗活跃主题缓存)+ `.keypulse/log.md`(聚类决策 append-only 日志)
- **借鉴**:Karpathy 持久化 wiki + nashsu 四信号亲和度 + SamurAIGPT 矛盾标记 + claude-obsidian Hot cache & Lint + migration_package 加速度
- **触达**:HUD 顶部 banner "本周回声 →",**点击跳转成功后永久隐藏**,跳转失败不隐藏

---

## 1 · 调研结论 · 哪些借,哪些不借

### LLM Wiki 类项目横向调研(4 个)

调研了 4 个 LLM-Wiki / Claude-Obsidian 项目,提取每个的可借鉴算法。

| 项目 | embedding? | 主题归并方式 | Obsidian 集成 | 可借鉴的核心 |
|------|------------|--------------|---------------|--------------|
| Karpathy gist | 否 | schema + log 手工审计 | N/A | log.md append-only 决策日志 |
| nashsu llm_wiki(中文) | 可选 LanceDB | **四信号亲和度模型** + Louvain 社团检测 | 否 | **四信号合并算法** + SHA256 增量缓存 |
| SamurAIGPT llm-wiki-agent | 未明确 | 实体/概念类型 + 源共享度 | 否 | **入库时显式矛盾标记** |
| **claude-obsidian** ★ | 否 | 语义抽取 + timestamp | **原生集成** | **Hot cache** + Base 插件视图 + **8 类 Lint** |

#### Karpathy gist

抓到的 3 步算法:**Ingest → Query → Lint**

借:
- ✅ 持久化 markdown 知识网,增量更新
- ✅ **不用 embedding / 向量库**,纯 markdown + 长 context
- ✅ `log.md` append-only 思想:每次聚类决策可追溯
- ✅ Lint 思想:定期找空主题 / 孤立事件 / 矛盾

不借: ❌ 通用问答场景 ❌ 跨问题复用页面的复杂度

#### nashsu llm_wiki

借:
- ✅ **四信号亲和度模型**(权重已按 KeyPulse 调整,见 §4.5):用于 weekly reconcile 阶段判断"两个 daily 各归一类但其实是同一件事"
- ✅ **两步 CoT 入库**(分析 → 生成):用在 §5.1 daily 聚类+重写合并 prompt
- ✅ **SHA256 增量缓存**:daily 内容不变则跳过重新聚类(M2 优化)

不借: ❌ Louvain 社团检测(对 KeyPulse 太重) ❌ embedding(明确按 Karpathy 路线纯 markdown)

#### SamurAIGPT llm-wiki-agent

借:
- ✅ **入库时显式矛盾标记**(不是查询时才发现):daily 阶段 LLM 怀疑两个 topic 该合并 → 在 frontmatter 标 `merge_candidate_with: [slug2]`,weekly reconcile 时统一处理
- ✅ "新源使 wiki 更富" 启发式:决定 daily 新事件该开新 topic 还是归现有

不借: ❌ 多类型 page(实体页/概念页/综合页 — KeyPulse 只需 Topics 一种)

#### claude-obsidian ★(跟 KeyPulse 场景最近)

借(均纳入设计):
- ✅ **Hot cache 概念** → `.keypulse/hot.md` 记本周活跃 topic,timestamp 排序,daily 末尾刷新,weekly 直接读
- ✅ **Operation log** → `.keypulse/log.md` append-only 聚类决策日志
- ✅ **8 类 Lint**(M2):孤立 topic / 死链 / 过时 / 缺交叉引用 / 矛盾未解决 / ...
- ✅ Obsidian REST API 双向同步思路(M2 探索:用户在 Obsidian 编辑 topic 后反写回 .keypulse/)

不借(M1): ❌ Base 插件 dashboard(M1 用纯 markdown 即可,M2 看是否要)

### migration_package 月报

| 借 | 不借 |
|----|------|
| ✅ **加速度** = 本期 delta − 上期 delta,判断"加速/平稳/退潮" | ❌ SCQA 框架(B 端汇报腔,笔友不打这个) |
| ✅ **双层候选**:规则层 + LLM 层,priority_score 合并 | ❌ 6 KPI 表(KeyPulse 没 KPI,也不该有) |
| ✅ **三禁三必 prompt 约束**(禁评判/禁套话/禁编造,必引用原文) | ❌ Top5 技能域矩阵 |
| ✅ 分层种子采样:高信号优先 | |
| ⚠️ 新鲜度衰减(改:衰减"对同一主题的同类观察",**不**衰减主题本身) | |

### Obsidian 现场审计(独立调研)

vault `/Users/Harland/Go/Knowledge/` 当前问题:

1. **Daily 的「今日主线」段两天都是空** —— HUD 之前以为拉这个段,实际拉的是「今日做的事」下零散 H3
2. **Topics/ 有 80 张卡,大量是 0 字节空壳或仅 frontmatter** —— 自动聚类质量参差,人不知道哪些是"真主题"
3. **事件卡标题机器味重**:`1123-make-app-实际成功了-bundle-已在-dist-preflight-在-venv-没装-8b907d77` —— 时间+原话+hash,不是给人读的
4. **「片段-」前缀身份不清**:`片段-0010-1599b9d5.md` 正文只有一行 `/Applications/KeyPulse.app`,不知该不该当事件
5. **Projects/ 是手写死文档**,Daily/Topics 都不链接它,形成两套平行系统
6. **粒度不齐**:同一天的 H3 从「修代码改 3 个文件」到「在 Codex 问一句话」混在一起
7. **「今日概览」开头 3 段已经 LLM 聚过类了**,但下面 H3 没回写、没合并 —— 聚类成果没保留

**结论**:周报的债是 daily 的债。聚类管线必须先修 daily,周报才有干净底座。

---

## 2 · 产品价值 · 双模式定位

### 2.1 客观记录模式(默认主体)

**解决的问题**:"我自己懒得整理 daily,但有时候真的需要复用素材"

**典型场景**:
- 周一 standup 要说"上周我做了啥" → 直接复制几段
- 项目 review 要找"过去一周这块进展" → 主题维度找
- 三个月后回看"我那段时间在忙什么" → 主题叙事比 7 篇日报清晰

**LLM 角色**:**重写**,不评判不洞察。把零散事件按主题聚合后写成连贯叙事。

**输出**:weekly.md 主体,按**主题**(不是按天)组织,每主题一段进展叙事 + `[[wiki link]]` 回引。

### 2.2 探索者模式(可选段,默认折叠)

**解决的问题**:"笔友看见但我没看见的"

**LLM 角色**:在客观记录之上再加一层挖掘。

**输出**:weekly.md 顶部 callout(可折叠),含两条:
- **没接住的球**:周一你说想做 X,做了吗?
- **一个观察**:笔友看见的一个温和提问,不下结论

### 2.3 两个板块 · 在 weekly.md 里顺序排列

不折叠,不藏 callout。两个板块在同一文件顺序写,客观记录在前(主体),探索者在后(深度)。共用一份聚类管线。

```
weekly/2026-W18.md

# 这周 (2026-W18)

## 这周的主线           ← 客观记录板块
### {主题 1} (★加速)
段落叙事 + [[wiki link]]
### {主题 2} (新冒头)
...

## 这周的回声           ← 探索者板块
### 没接住的球
- 周一你说想做 X,后面没看到 [[wiki link]]
### 一个观察
一句温和提问 ?[[wiki link]]

> [!note] 我的批注
> (空,M2 写回长期记忆)
```

成本 ≈ 一次聚类 + 主题级 LLM(批量)+ 探索者 LLM 一次。

---

## 3 · 数据架构重构(必做)

### 3.1 当前混乱图

```
Daily/2026-05-05.md
  ├─ ## 今日概览       ← LLM 聚过类的 3 段(成果浪费,没回写下面)
  ├─ ## 今日做的事
  │    ├─ ### H3 (13 条零散,粒度不齐)
  │    └─ ...
  ├─ ## 今天的事件卡   ← 列 [[Events/2026-05-05/xxx]]
  ├─ ## 今天涉及的主题 ← 空
  └─ ## 今日主线       ← 空 ❌

Events/2026-05-05/         ← 19 个独立 md,标题机器味
  ├─ 1123-xxx-hash.md
  ├─ 片段-0010-hash.md     ← 身份不清
  └─ ...

Topics/                    ← 80 张,大量空壳
  ├─ make-app-xxx.md       ← 自动生成,命名混乱
  └─ carmind-cce-xxx.md    ← 23 关联事件但卡本身 0 字节

Projects/keypulse/         ← 手写文档,孤岛,无反链
  ├─ README.md
  └─ TESTING.md
```

### 3.2 重构后的关系图

```
        ┌─────────────────────────────────────────────────┐
        │   ☆ 用户视角 (只看这两个)                       │
        ├─────────────────────────────────────────────────┤
        │                                                 │
        │  Daily/{date}.md          每日叙事入口          │
        │  ## 今日主线                                    │
        │    ### {主题} (聚类后)                          │
        │      · 进展叙事                                 │
        │  ## 散点 (未聚类)                               │
        │                                                 │
        │  Weekly/{week}.md         每周主题级叙事        │
        │  ## 这周的主线 (客观记录板块)                   │
        │  ## 这周的回声 (探索者板块)                     │
        │  > [!note] 我的批注                             │
        │                                                 │
        └─────────────────┬───────────────────────────────┘
                          ▲
                          │  生成 / 增量更新
                          │
        ┌─────────────────┴───────────────────────────────┐
        │   ☆ 内部数据层 (用户不直接看,放 .keypulse/)    │
        ├─────────────────────────────────────────────────┤
        │                                                 │
        │  .keypulse/events/{date}/{...}.md               │
        │     原子事件(5 分钟级)                          │
        │                                                 │
        │  .keypulse/topics/{slug}.md                     │
        │     跨时间主题聚合 · keywords[] · entries[]     │
        │     · status (new/active/ongoing/dormant)       │
        │                                                 │
        │  .keypulse/hot.md          ← 滑窗缓存           │
        │     最近 7 天活跃主题 (借鉴 claude-obsidian)    │
        │     供 weekly 快速读取,避免全表扫               │
        │                                                 │
        │  .keypulse/log.md          ← 聚类决策日志       │
        │     append-only,记录每次 LLM 归类决策           │
        │     失败可追溯 (借鉴 Karpathy / nashsu)         │
        │                                                 │
        └─────────────────────────────────────────────────┘
```

**说明**:
- Obsidian 默认不索引以 `.` 开头的目录,用户在 vault 里完全看不到 `.keypulse/`
- 程序读写 `.keypulse/` 不影响用户的 Daily/Weekly 体验
- `Projects/` 是用户自己放的内容,本设计不动也不依赖

### 3.3 卡的职责 · 用户视角 vs 内部数据

#### 用户看的两类(只这两类)

| 卡类型 | 职责 | LLM 角色 | 生命周期 |
|--------|------|---------|----------|
| **Daily/{date}.md** | 一天的叙事入口 | 聚类 + 重写(合并一次调用) | 永久 |
| **Weekly/{week}.md** | 一周的主题级叙事 | 聚类 reconcile + 双板块写作 | 永久 |

#### 内部数据(用户不直接看,放 `.keypulse/` 隐藏目录)

| 文件 | 职责 | 形态 |
|------|------|------|
| `.keypulse/events/{date}/...` | 原子事件 | 自动采集,机器索引 |
| `.keypulse/topics/{slug}.md` | 跨时间主题聚合 | LLM 维护,frontmatter + entries[] |
| `.keypulse/hot.md` | 滑窗活跃主题缓存 | 每日 daily 末尾刷新 |
| `.keypulse/log.md` | 聚类决策日志 | append-only,可追溯 |

### 3.4 关键调整(相对现状)

| 调整项 | 现状 | 调整后 |
|--------|------|--------|
| Events / Topics 位置 | 在 vault 顶层(用户能看到) | 全部迁到 `.keypulse/` 隐藏目录,Obsidian 不索引 |
| Events 文件名 | `1123-{原话片段}-{hash}` | `{HHmm}-{id}.md`,人话标题放 frontmatter `title` 字段 |
| 「片段-」前缀 | 不知什么意思 | **取消**,统一为 Event,frontmatter `kind: snippet \| dialogue \| action` 区分 |
| Topics 空壳 | 80 张,大量 0 字节 | 一次性 lint 清理:< 3 entries 且 last_seen > 30 天 → 归档到 `.keypulse/topics/_archived/` |
| Daily 「今日主线」 | 空 | **每日 18:00 聚类后自动填** |
| Hot cache | 无 | 新增 `.keypulse/hot.md`,记本周活跃主题 |
| 决策日志 | 无 | 新增 `.keypulse/log.md`,append-only |

---

## 4 · 聚类算法 · 主题主导,时间辅助

### 4.1 核心原则

> "聚类不是按时效,而是按事儿/主题来看的,时间是第二位的。" —— Harland

**含义**:
- 同一件事(比如"修 KeyPulse HUD 权限")跨 12 小时甚至跨天,仍是一个 cluster
- 时间相邻但主题不同(00:13 问 Codex 微调 vs 00:15 切去查百科),**不**聚一起
- 时间只在主题信号缺失时做兜底

### 4.2 证据分层 · 硬证据建图,软证据二次校验

> "从哪个 APP 里搜到的是'源',这是硬证据。时间点对不对也算硬证据。" —— Harland

**相对原"三层信号"的修正**:把"采集源/上下文"和"时间邻近"从兜底层提升到硬证据层 —— 看到即可连边,不依赖 LLM 判断。原"三层"实际上把硬软混在一起了。

#### 硬证据(看到即连边,无需 LLM)

| 层 | 证据 | 例子(用户给的真实 case) |
|---|---|---|
| **H1** 共享 entity ID | git commit hash / 文件绝对路径 / URL(去 query) / 命名实体抽取 | 5/6 daily 中"提交 21c290d"和"修改 monitor_html.py"两条事件都涉及同一文件和同一 commit → 强连边 |
| **H2** 共采集源 / 共上下文 | 同 app bundle id + window title、同 Claude/Codex session id、同 Chrome tab url、同 Terminal 会话/shell pid | 同一个 Claude Code 会话内的所有事件 → 天然相关,强连边 |
| **H3** 时间邻近 | Δt < 5min 直连;5–30min 需 H1 或 H2 加成;>30min 仅在强证据下才连 | "00:05 commit"和"凌晨刚过零点改代码"时间贴着 → 强连边 |

#### 软证据(M1 范围内只用 S2,S1 不用)

| 层 | 证据 | M1 状态 | 用途 |
|---|---|---|---|
| ~~S1 文本语义相似(embedding)~~ | ~~标题/正文 embedding cos~~ | **M1 不用** | 跟 §1 调研结论"按 Karpathy 路线纯 markdown,不用 embedding"保持一致。跨连通分量合并交给 Pass-2 LLM 主动识别 |
| S2 因果链模板 | "改文件→跑测试→commit→push"等 dev/写作/调研工作流 pattern | M1 用 | 给同一聚类内事件**排序**叙事顺序,不参与连边 |

**M1 跨连通分量合并怎么做(无 embedding 替代方案)**:Pass-2 LLM 看到所有连通分量后,prompt 里明确要求"识别看似不同分量但其实是同一件事的情况,标 merge"。靠 LLM 上下文里的 keyword 重叠和 entity 命中判断。M2 如有需要再考虑加轻量 embedding。

#### 聚类骨架

```
[Step 1] 硬证据建图
  对每对事件 (e_i, e_j):
    if H1 命中 (共享文件/commit/URL/实体)         → 加边
    if H2 命中 (共 session/app+window/tab)         → 加边
    if H3 命中 (Δt < 5min)                          → 加边
    if H3 边缘 (5–30min) 且 (H1 或 H2 命中)         → 加边
    其他                                            → 暂不连
  → 求连通分量 = 初始聚类候选

[Step 2] S2 工作流模板排序(可选)
  对每个连通分量内,识别"改文件→跑测试→commit→push"等已知模板,
  按模板顺序排序事件,产出叙事链
  注:跨分量合并交给 Pass-2 LLM,M1 不做程序层 embedding 二次校验

[Step 3] 显式锚点 overwrite(超强)
  HUD inline input "今天最想完成 X" → 当天命中 X 关键词的所有事件强制归 X 主题

[Pass-2] LLM 整体决策(方案 C 合并调用,详见 §4.3)
  输入: Step 1-3 后的聚类候选 + 现有 Topics 索引 + HUD input
  任务:
    a. 裁决 merge_candidate 是否真合并
    b. 给每个聚类匹配现有 Topic / 开新 / 标 Misc(离题)
    c. 生成新主题的 slug + display_name + keywords
    d. 同时写好叙事(因为是合并调用)
  输出: JSON {clusters: [{topic, narrative, events[]}], misc: [...]}
```

**关键**:LLM 不做"哪些事件聚一起"的判断,这是硬证据建图就解决的;LLM 只做"聚好的类归到哪个主题 + 怎么写"。

#### 时间窗初值(M1 边跑边校准)

| Δt | 连边规则 |
|---|---|
| < 5 min | 直连 |
| 5–30 min | 需 H1 或 H2 加成 |
| > 30 min | 仅强证据(同 entity / 同 session)下连 |

阈值是拍脑袋初值,跟 nashsu affinity 阈值 5.0 一并在 M1 上线后两周用真实数据校准(见 §8 风险 #8)。

### 4.3 聚类时机 trade-off · 推荐方案 C

User 提的关键问题:**"LLM 每次 review 内容时是否顺带把聚类做掉?还是先重写后聚类?"**

#### 三方案对比

**方案 A: 先重写后聚类**
1. LLM 读原始事件 → 写「今日概览」叙述
2. LLM 再读 H3 → 聚类到主题
- ❌ 重写阶段不知主题归属,叙事可能与最终聚类不一致
- ❌ 两次 LLM 调用,且第二次还要回头改第一次的输出
- 这是 **当前 daily 系统的现状**(「今日概览」聚过类但没回写下面)

**方案 B: 先聚类后重写**
1. LLM 读原始事件 + 现有 Topics 索引 → 聚类映射
2. LLM 按主题分组 → 写每个主题段
- ✅ 叙事按主题组织,连贯
- ❌ 两次 LLM 调用,token 略多

**方案 C: 聚类与重写合并一次 LLM 调用 ⭐ 推荐**
1. LLM 一次性输入: 原始事件 + Topics 索引 + HUD input
2. LLM 输出 JSON: `{clusters: [{topic, narrative, events[]}], misc: [...]}`
3. 后处理: upsert Topics + 渲染 Daily 「今日主线」段
- ✅ 1 次调用,token 最省
- ✅ 聚类和叙事天然一致(同一次推理)
- ✅ 借鉴 nashsu **两步 CoT** —— prompt 里明确"先在内部分析,再输出聚类+叙事"
- ⚠️ Prompt 复杂度高,LLM 可能在长任务里出错 → 用 JSON schema 约束 + retry 兜底

#### 推荐: 方案 C,理由

- daily 一天事件 ≤ 30 条,token 量小(<5K),1 次 LLM 完全装下
- 聚类和叙事一致性是关键,合并比分两步可控
- 成本/时延更低
- nashsu 项目验证了 CoT 拆分(分析 → 生成)在同一次调用里可行

#### 算法效率(采用方案 C)

> 估算前提:topics 索引必须裁剪后再送 LLM,否则 200 主题 × ~80 tokens/条 = 16K,加上事件 5K+ 轻松破 20K,方案 C "token 最省"的优势就没了。

#### Topics 索引裁剪策略(方案 C 必备前置)

LLM 输入只送以下 topics 子集,不送全量索引:

1. **Hot 主题**(`.keypulse/hot.md` 里的近 7 天活跃主题,~10-20 个)
2. **关键词命中候选**:用当天事件标题/正文的关键词,在 topics 全量索引里做 substring/jaccard 命中,top 10
3. **沉睡复活候选**:last_seen 在 14-90 天前 且 keyword 命中本日事件 的主题(防止"PairDrop 14 天前出现过"被当成新主题),top 5
4. 上述三类去重合并,**硬上限 30 个主题**送 LLM,每个只带 `{slug, display_name, keywords[5], last_seen}`(约 50 tokens/条),不带 last_3_entries 摘要

裁剪后 topics 部分 token = 30 × 50 ≈ 1500 tokens。

#### 估算修正后

| 阶段 | LLM 调用 | 输入 token(修正) | 期望耗时 |
|------|----------|----------------|----------|
| Daily 18:00 首版(聚类+重写合并) | **1 次** | topics ~1500 + 事件 ~3000 + HUD 锚点 ~200 = **~4700** | < 8s |
| Daily 增量(每 90 min / 5 事件) | **0 次** | (规则层分配) | < 0.5s |
| Daily 23:30 收尾 | **1 次** | ~3000 | < 5s |
| Weekly 主题级 reconcile | 1 次 | hot.md 主题 ~1500 + 跨天候选 ~1500 = **~3000** | < 5s |
| Weekly 客观记录(批量主题) | 1 次 | 7 天 daily 摘要 ~5000 + topics ~1500 = **~6500** | < 15s |
| Weekly 探索者 | 1 次 | ~4000 | < 8s |
| **Weekly 总耗时** | **3 次 LLM** | | **< 30s** |

**关键**:daily 阶段已把聚类活做完,weekly 只 reconcile 跨天 + 写叙事。不在 weekly 阶段重新聚类。

**风险**:裁剪策略可能漏候选(关键词命中没覆盖到的相关主题)。M1 上线后两周用 log.md 里的"应合未合"案例校准裁剪规则。

### 4.4 主题状态标签(规则层算,不调 LLM)

```
对每个本周出现的 Topic,基于 entries[] 历史算:

new          (新冒头)     : first_seen 在本周内
accelerating (加速)        : 加速度 = (本周次数 − 上周次数) − (上周次数 − 上上周次数) > 0 且本周次数 ≥ 2
steady       (持平)        : 本周与上周次数差 ≤ 1
declining    (退潮)        : 本周次数 < 上周次数 且 本周次数 < 2/3 平均
revived      (沉睡复活)    : last_seen > 14 天前 且本周次数 ≥ 1
ongoing      (持续主线)    : 连续 ≥ 4 周每周都出现 → 长期主题,**永不降权**
```

`ongoing` 标签是回应反驳 #1:**长期重要的事不被隐去**,反而单独标"持续主线"突出。

### 4.5 Daily 聚类 ↔ Weekly 聚类 · 时间线维度

User 提问:**"每天聚类和每周聚类的关系,融入时间后的关系,是否需要像 migration_package 一样参考时间线"**

回答:**两层聚类层级递进,不重做。时间是辅助维度,不是聚类主导。**

#### 层级递进流

```
[Daily 聚类]                          [Weekly 聚类]
本天 30 条事件                        本周 5-7 个 daily 已聚好的主题
        │                                       │
        ▼                                       ▼
读 .keypulse/topics/ 索引             读 .keypulse/hot.md (滑窗缓存)
        │                                       │
        ▼                                       ▼
LLM 一次决定: 归现有主题/开新/misc   主题级 reconcile + 状态打标 + 加速度
        │                                       │
        ▼                                       ▼
upsert topics/{slug}.md               不再调 LLM 做聚类(daily 已做)
渲染 Daily 「今日主线」段             仅在跨天发现"daily 各自归类但其实是
追加 hot.md (本周活跃主题)             同一件事"时,LLM 调一次 reconcile
追加 log.md (决策日志)                调用四信号亲和度算分(借鉴 nashsu)
```

#### Daily 聚类时是否看时间线?

看,但只**作为参考输入**,不主导:
- LLM 输入里的 `existing_topics` 索引带 `last_3_entries` 字段(含日期)
- 这给 LLM 时间感(主题是活跃 / 沉寂),避免今天孤立判断
- 例:今天首次出现"PairDrop",但 Topics 里若有"跨设备文件传输"主题 last_seen=14 天前 → LLM 应识别归一,而不是开新主题

#### Weekly reconcile 用什么算法判断"daily 各归一类但其实是同一件事"?

**借鉴 nashsu 的四信号亲和度模型**(权重已按 KeyPulse 场景调整,主题主导):

```
亲和度(topic_a, topic_b) = 
    keyword 重叠 ×3.0      # 主题主导 (原 nashsu ×1.5)
  + 共享实体(人/产品/项目) ×2.5
  + 共同关联事件 ×1.5
  + 共享时间段 ×1.0      # 时间辅助 (原 nashsu ×4.0,降权)

如果 总分 ≥ 阈值 5.0 → 候选合并
LLM 二次确认: "这两个主题描述同一件事吗?"
确认合并 → 在 .keypulse/log.md 记录 "merged: a → b"
```

**与 nashsu 的关键差异**:
- nashsu 共享时间段权重 ×4(他们做长期文档库,时间是强信号)
- KeyPulse 共享时间段权重 ×1(我们做日记,主题主导,时间辅助)

#### 加速度计算(借鉴 migration_package 时间线)

```
对每个 ongoing/active topic:
  本周 mentions = 本周该主题 entries 数
  上周 mentions = 上周该主题 entries 数
  上上周 mentions = 上上周该主题 entries 数
  
  delta_this = 本周 mentions − 上周 mentions
  delta_last = 上周 mentions − 上上周 mentions
  acceleration = delta_this − delta_last
  
  status:
    acceleration > 0 且 本周 mentions ≥ 2  → accelerating
    acceleration < 0 且 delta_this < 0     → declining
    |acceleration| ≤ 1                     → steady
```

**用上上期数据判断速度,而非仅看单期环比** —— 这是 migration package 月报的核心算法,直接复用。

#### 时间在算法里的位置

> 注:此表是 **weekly 主题级 reconcile** 的权重,跟 §4.2 daily 事件级聚类的硬/软证据是两件事。daily 事件级里时间(H3)是硬证据;weekly 主题级里时间(共享时间段)是软辅助 —— 因为 weekly 比的是"两个主题描述同一件事吗",光时间贴近没意义。

| 维度 | 角色 | 权重 |
|------|------|------|
| 主题语义(关键词/实体) | 主导 | ×3.0 |
| 显式锚点(HUD input) | 超强,可 overwrite | × ∞ |
| 共享实体 | 强辅助 | ×2.5 |
| 时间邻近 | **辅助兜底**(只在主题信号缺失时打破平局) | ×1.0 |
| 加速度/状态打标 | 时间线滑窗产物,**不参与聚类**,只标注主题状态 | N/A |

### 4.6 采集字段现状 · M0 必修的断层

§4.2 的硬证据建图依赖 raw_events 表里有这些字段。Codex 摸底(2026-05-06)结果:

#### 字段现状

| 硬证据维度 | 字段 | 状态 | 落点 | 备注 |
|---|---|---|---|---|
| H1 文件路径 | git repo / Claude project_dir / markdown 路径 | **采了但未入 raw_events** | `SemanticEvent.artifact` / `metadata.repo_path/project_dir` | 走 `sources/`,只产 SemanticEvent |
| H1 git commit hash | full hash | **采了但未入 raw_events** | `SemanticEvent.metadata.full_hash` | 同上 |
| H1 URL | Chrome/Safari history | **采了但未入 raw_events** | `SemanticEvent.full_url`(去 query) | raw browser watcher 写了 `metadata.url` 但没去 query |
| H1 命名实体 | — | **完全缺失** | — | 只有运行时 `Entity` 对象,无存储字段 |
| H2 app bundle id | window/AX/OCR | 部分(写在 `process_name`,跟 app name 混) | `raw_events.process_name` | 不是独立列,需后处理 |
| H2 window title | window/AX/OCR/browser | 满 | `raw_events.window_title` | 可用 |
| H2 Chrome tab url | browser watcher | 部分(带 query) | `raw_events.metadata_json.url` | 需在聚类前去 query |
| H2 Claude session id | claude_code source | **采了但未入 raw_events** | `SemanticEvent.metadata.session_id` | 同 H1 断层 |
| H2 Codex session id | codex_cli source | **采了但未入 raw_events** | 同上 | 同 H1 断层 |
| H2 Terminal session/shell pid | — | **完全缺失** | — | zsh_history 只有命令+时间,无 session/pid |
| H3 timestamp | 全部 watcher | 满 | `raw_events.ts_start/ts_end` | UTC ISO,精度足够 |

#### M0 必修(开工前置)

聚类管线写起来之前必须先修这两件事,否则硬证据建图大半失效:

1. **断层 1 · sources → raw_events 写入路径**:`sources/`(git/claude_code/codex_cli/chrome_history/safari_history/markdown_vault)目前只产 SemanticEvent,不调 `insert_raw_event()`。需要补一条统一的 sink,把 `SemanticEvent` 里的 `artifact / metadata.session_id / metadata.full_hash / metadata.repo_path / full_url` 等映射到 raw_events 的列或 metadata_json
2. **断层 2 · raw_events schema 扩列或约定 metadata_json 子结构**:推荐在 `metadata_json` 里规约一个 `entities: {commit_hash?, file_paths: [], urls: [], session_id?, app_bundle_id?, ...}` 子对象,聚类管线读它。这比加列改 schema 风险小

#### 缺失字段的妥协

- **H1 命名实体抽取**:M1 用现有 `entity_extractor.py` 的 regex/启发式产出,作为 raw_events 的 metadata 子字段。不在 M1 加 NER 模型
- **H2 Terminal session id**:M1 不补,terminal 操作通过 `process_name + window_title + 时间窗`(H3)兜底。后续如有需要再加 watcher
- **H2 app bundle id 与 app name 分离**:M1 沿用 `process_name`,聚类管线接受这种粗粒度;M2 再做拆分

#### 当前 schema 跑聚类管线的判断

**凑合**(codex 原话)。修完上面两个断层后才到"够用"。

#### 期望值管理 · M1 上线后的硬证据效力分布

§4.2 把 H1/H2/H3 都列为硬证据,但 M1 上线时三者的实际命中率差距很大,不要等 H1 兜底:

| 维度 | M1 期望命中率 | 主要靠它连边的事件类型 |
|---|---|---|
| **H2 共采集源** | 高(60-80%) | 同 Claude/Codex 会话内、同 app+window、同 Chrome tab 内的连续操作 —— **M1 主力** |
| **H3 时间邻近** | 高(几乎 100%,timestamp 是满字段) | 5min 内事件直连;辅以 H1/H2 加成扩到 30min |
| **H1 文件路径 / commit hash / URL** | 中(30-50%) | 仅在 git/markdown/browser 等 sources 触发的事件上有,M0 修完后才到这水位 |
| **H1 命名实体抽取** | 低(20-30%,regex 兜底) | 仅在标题/正文有项目名/产品名/人名出现的事件上 —— **M1 是 bonus,不是主力** |

**M1 实际效果预期**:绝大部分聚类边由 H2(session/window)+ H3(时间)产生,H1 的两个子项是补强,不是主力。如果上线后聚类质量差,**先排查 H2 数据采集是否真在 raw_events 里**,而不是怀疑 H1 实体抽取。M2 再把命名实体单独做(NER 模型或更精准的 LLM 抽取)。

---

## 5 · Prompt 约束清单(机器可执行版)

**原则**:不用"温和"、"自然"、"不评判"这种主观词。每条规则必须可被代码 `string.contains()` / 正则 / 长度检查 验证。LLM 输出后程序逐条校验,不达标 retry,3 次后用 fallback。

### 5.1 Daily 聚类+重写合并 prompt(方案 C 核心)

**调用时机**:每天 18:00 首版 + 23:30 收尾(同一 prompt,输入数据不同)

```yaml
INPUT (JSON):
{
  "scope_date": "2026-05-05",
  "events": [
    {
      "id": "e_xxx",
      "timestamp": "2026-05-05T11:23:00+08:00",
      "app": "Claude.ai",
      "kind": "dialogue|action|snippet",
      "content_full": "..."
    }
    // 一天 ≤ 30 条
  ],
  "existing_topics_index": [
    {
      "slug": "keypulse-hud-fix",
      "display_name": "KeyPulse HUD 权限修复",
      "keywords": ["keypulse", "hud", "权限", "tcc", "accessibility"],
      "last_3_entries": [
        {"date": "2026-05-04", "summary": "..."},
        ...
      ],
      "status": "ongoing|active|dormant"
    }
    // 通常 50-200 个
  ],
  "hud_input_today": "今天最想完成: 把 HUD signals 改吃日报主题",  // 可为 null
  "hot_cache": [/* 本周已活跃主题 slug 列表 */]
}

INTERNAL THINKING (CoT, 不输出):
1. 对每个 event,先匹配 keywords (substring) → 找候选 topic
2. 命中多个 → 取 keywords 命中数最高的;平局取 last_3_entries 最近的
3. 没命中且 ≥ 2 个 events 共享实体 → 候选 new topic
4. 单点离题 → misc

OUTPUT (JSON):
{
  "clusters": [
    {
      "topic_slug": "keypulse-hud-fix",
      "action": "existing",                  // existing | new
      "display_name": null,                  // action=new 时必填
      "keywords": null,                      // action=new 时必填
      "event_ids": ["e_xxx", "e_yyy"],
      "narrative": "..."                     // 见下方校验规则
    }
  ],
  "misc_event_ids": ["e_zzz"],
  "merge_candidate_pairs": [["slug_a", "slug_b"]]  // LLM 怀疑该合并但拿不准
}

OUTPUT 校验规则(程序逐条 check,不达标 retry):

[每个 cluster.narrative]
- 长度: 30 ≤ len(narrative) ≤ 120
- 必须含至少 1 个 "[[YYYY-MM-DD]]" 模式 (regex: \[\[\d{4}-\d{2}-\d{2}\]\])
- 黑名单子串(任一出现即 fail):
    "高效", "低效", "做得好", "成功完成", "失败", "卓有成效",
    "如火如荼", "紧锣密鼓", "齐头并进", "聚焦", "赋能",
    "突破", "瓶颈", "复盘",
    "建议", "应该", "必须", "推荐", "需要",
    "下周", "未来", "接下来", "计划",
    "显然", "肯定", "无疑", "终于", "完全"
- 数字检查: narrative 里出现的数字必须能在 events[i].content_full 里 substring 找到
- 时态检查: 必须含至少 1 个过去式动词,白名单子串(任一即可):
    "改了", "写了", "讨论", "访问", "提交", "修复", "遇到", "查", "记下",
    "发现", "决定", "试", "更新", "删", "加"

[action="new"]
- topic_slug 正则: ^[a-z][a-z0-9-]{2,40}$ (ASCII 小写连字符)
- display_name 长度: 8 ≤ len ≤ 20 (中文字符)
- keywords: 5 ≤ count ≤ 10, 全 ASCII 小写
- topic_slug 不得与 existing_topics_index 中任一 slug 完全相同
- keywords 与 existing_topics_index 中任一 topic 的 keywords 重叠 ≥ 3 个 → 拒绝(应该归 existing)

[merge_candidate_pairs]
- 仅当 LLM 在 CoT 阶段识别到候选合并但置信度 < 0.8 时填
- 走 weekly reconcile 阶段最终决定

[整体]
- 输出必须是 valid JSON
- clusters 数量上限 10(超过说明聚类失败,retry)
- 单个 cluster 的 event_ids 数量上限 = events 总数(防止 LLM 随便塞)
```

### 5.2 探索者板块 prompt(机器可执行版)

**调用时机**:周五 23:35,生成 weekly 时一次

```yaml
INPUT (JSON):
{
  "scope_week": "2026-W18",
  "weekly_dailies": [
    {"date": "2026-05-04", "content_full": "...完整 daily.md 内容..."}
    // 5-7 篇
  ],
  "topic_status": [
    {
      "slug": "...",
      "display_name": "...",
      "status": "new|accelerating|steady|declining|revived|ongoing",
      "weekly_count": 3,
      "last_week_count": 1
    }
  ],
  "hud_inputs_this_week": [
    {"date": "2026-05-04", "content": "今天最想完成 X"}
    // 用户填写的天数,可能 < 7
  ],
  "last_week_observation_text": "..." // 上周"一个观察"的文本,可为 null
}

OUTPUT (JSON):
{
  "missed_balls": [
    {
      "what": "...",                        // ≤ 30 字
      "anchor_link": "[[2026-05-04]]"
    }
    // 0-3 条
  ],
  "observation": {
    "text": "...",                          // 见校验
    "anchor_link": "[[2026-05-04]]",
    "anchor_quote": "..."                   // 必须是 weekly_dailies[i].content_full 的 substring
  }
}

OUTPUT 校验规则(程序逐条 check):

[missed_balls 触发条件 — 必要,不达标输出 []]
- 对每条 hud_inputs_this_week[i]:
  · 提取 input.content 的实体词(项目名/产品名/动词)
  · 检查 topic_status 里是否有 slug 包含该实体词的 active topic
  · 不存在 → 候选 missed ball
- 输出 what 字段格式必须匹配模板:
    "周{X}你说想 {action},后面没看到"
  其中 {X} 在 [一,二,三,四,五,六,日] 中
- anchor_link 必须是 hud_inputs_this_week[i].date 之一

[observation.text 校验]
- 长度: 10 ≤ len ≤ 60 (中文字符)
- 最后一个非空白字符必须是 "?" 或 "?"
- 黑名单子串(任一出现即 fail):
    指令词: "建议", "应该", "必须", "下周", "可以", "需要", "请", "推荐"
    管家腔: "高效", "效率", "产出", "赋能", "突破", "瓶颈", "复盘", "聚焦"
    自我中心: "我注意到", "我发现", "我看见", "笔友我"
    绝对词: "肯定", "显然", "无疑", "完全"

[observation.anchor_quote 校验]
- 必须能用 substring 匹配 weekly_dailies[i].content_full 中的某段
- 长度 ≥ 10 字 ≤ 80 字
- 不能是 frontmatter / 标题 / 空行

[与上周去重]
- 如 last_week_observation_text != null:
  observation.text 不得与 last_week_observation_text 共享 ≥ 5 个连续字符 substring
- 违反则 retry,3 次失败后输出 fallback:
  {"text": "本周笔友没看见值得问的事。", "anchor_link": null, "anchor_quote": null}

[空 fallback]
- 如 hud_inputs_this_week == [] → missed_balls 直接输出 []
- 如 topic_status == [] (本周没主题) → observation 用 fallback
```

### 5.3 Weekly 客观记录主题段 prompt(批量调用)

**调用时机**:周五 23:35,对 weekly top N 主题批量调一次

```yaml
INPUT (JSON):
{
  "topics_to_write": [
    {
      "slug": "...",
      "display_name": "...",
      "status": "new|accelerating|...",
      "weekly_entries": [
        {"date": "2026-05-04", "narrative_one_line": "..."}
      ],
      "previous_week_narrative": "..." // 可为 null
    }
    // top N, N ≤ 8
  ]
}

OUTPUT (JSON):
{
  "narratives": [
    {
      "slug": "...",
      "narrative": "...",        // 见校验
      "anchors": ["[[YYYY-MM-DD]]"]  // ≥ 1
    }
  ]
}

OUTPUT 校验规则(同 §5.1 narrative,加几条):

[每个 narrative]
- 长度: 30 ≤ len ≤ 120
- 黑名单子串: 同 §5.1
- 时态: 同 §5.1
- 至少 1 个 "[[YYYY-MM-DD]]",且日期必须是 weekly_entries 中存在的日期

[与上周去重]
- 如 previous_week_narrative != null:
  本次 narrative 不得与 previous_week_narrative 共享 ≥ 8 个连续字符
- 违反则 retry,3 次后用 fallback:
  "本周{display_name}延续上周节奏,见 {anchors[0]}"

[状态标签自然融入 — 字面规则]
- status="new" → narrative 必须含以下任一: "第一次", "首次", "开始", "新"
- status="accelerating" → 必须含: "更多", "频繁", "密集", "增多"
- status="declining" → 必须含: "少了", "减弱", "偶尔", "减"
- status="revived" → 必须含数字日期间隔表达,正则: 隔了\d+天 | \d+天后 | 沉寂\d+
- status="ongoing" → 必须含: "继续", "仍", "持续", "依然"
- status="steady" → 无强制
```

### 5.4 Weekly Topic Reconcile prompt(可选,仅当 daily 阶段标了 merge_candidate)

**调用时机**:周五 23:35,在客观记录前

```yaml
INPUT (JSON):
{
  "candidate_pairs": [
    {
      "slug_a": "...",
      "slug_b": "...",
      "affinity_score": 6.8,        // 程序算的四信号亲和度
      "topic_a_summary": {...},
      "topic_b_summary": {...}
    }
  ]
}

OUTPUT (JSON):
[
  {"action": "merge", "into": "slug_a", "from": "slug_b"},
  {"action": "keep_separate"}
]

判定规则:
- LLM 仅做"是否同一件事"判断
- 程序按 OUTPUT 执行 .keypulse/topics/ 文件操作
- 所有 merge 写入 .keypulse/log.md
```

---

## 6 · 完整数据流

### 6.1 Daily 生成 · 18:00 首版 + 增量更新

```
[每日 18:00 · daily 首版生成]  ← 用户拍的时点
  │
  ├─ 数据准备
  │   · 读 .keypulse/events/2026-05-05/*.md  (本日事件,通常 ≤ 30)
  │   · 读 .keypulse/topics/*.md 索引       (50-200 个,只取 frontmatter)
  │   · 读 .keypulse/hot.md                 (本周已活跃主题)
  │   · 读 HUD inline input (今天最想完成)
  │
  ├─ ★ LLM 调用 (方案 C: 聚类+重写合并 1 次)
  │   · Prompt: §5.1 Daily 聚类+重写
  │   · 输出 JSON: clusters[] + misc_event_ids + merge_candidate_pairs
  │
  ├─ 后处理
  │   · upsert .keypulse/topics/{slug}.md
  │       新主题 → 创建文件 (frontmatter + 首条 entry)
  │       旧主题 → 追加 entry (date + narrative_one_line)
  │   · 渲染 Daily/{date}.md
  │       ## 今日主线
  │         ### {主题 display_name} (聚类后,聚合本日所有相关事件)
  │           narrative + [[wiki link]]
  │       ## 散点                          ← misc 事件,简短列表
  │   · 刷新 .keypulse/hot.md (本周活跃主题,timestamp 排序)
  │   · append .keypulse/log.md (本次决策,可追溯)
  │   · HUD 改读 Daily 「## 今日主线」段下的 ### H3
  │
  └─ 章节范式锁定:本日范式自此固定,后续增量只追加不重写

[18:00 之后增量更新]
  触发条件: 新事件累积 ≥ 5 条 OR 距上次更新 > 90 min
  · 不调 LLM,走规则层 (§4 Layer 1-3 信号)
  · 命中现有 cluster → 追加到对应主题段尾
  · 不命中 → 暂存到 .keypulse/pending/,等 23:30 收尾
  · 不重写已有段落,只追加

[每日 23:30 · 收尾]
  · 处理 18:00 后所有 pending events
  · 一次小规模 LLM 调用(同 §5.1 prompt,只输入 pending 部分)
  · 合并到当日 Daily「今日主线」对应主题段
  · 写入 Topics entries[] 持久化
```

### 6.2 Weekly 生成 · 周五 23:35

> **触发时机修正**:原方案是周五 23:35,但 daily 18:00 才出首版,意味着周五的工作记录会**系统性缺失**。改为 **周五 23:35**(daily 23:30 收尾后 5 分钟),保证周五数据完整再生成 weekly。

```
[周五 23:35 · weekly 触发(daily 23:30 收尾后)]
  │
  ├─ 阈值检查
  │   · 本周日报数 ≥ 5? 否 → 走 6.3 fallback
  │
  ├─ Step A: 主题状态计算 (规则层,不调 LLM)
  │   · 拉本周所有 daily 命中的 Topics
  │   · 算每主题: 加速度 + status (§4.4)
  │   · 排序: new > accelerating > revived > ongoing > steady > declining
  │   · top N (N ≤ 8) 进入下一步
  │
  ├─ Step B: Topic Reconcile (条件性)
  │   · 收集本周 daily 阶段产出的 merge_candidate_pairs
  │   · 程序算四信号亲和度 (§4.5),score ≥ 5.0 进入 LLM
  │   · LLM 调 §5.4 prompt,输出 merge / keep_separate
  │   · 程序执行 .keypulse/topics/ 文件操作
  │
  ├─ Step C: 客观记录板块 (LLM 1 次,批量)
  │   · Prompt: §5.3 Weekly 客观记录主题段
  │   · 对 top N 主题批量生成 narratives
  │   · 校验失败 retry,3 次后 fallback
  │
  ├─ Step D: 探索者板块 (LLM 1 次)
  │   · Prompt: §5.2 探索者
  │   · 输出 missed_balls + observation
  │
  └─ Step E: 渲染 Weekly/{week}.md
      · 顶部: 元数据 (周序号 + 本周天数 + 主题总数)
      · ## 这周的主线 (客观记录板块)
        ### {主题} (★状态徽章)
        narrative + anchors[]
      · ## 这周的回声 (探索者板块)
        ### 没接住的球 (0-3 条)
        ### 一个观察 (1 条)
      · 底部: > [!note] 我的批注 (空,M2 写回 hot.md 长期记忆)
```

### 6.3 触达 · HUD banner

```
[周五 23:40]  weekly 写完后
  · HUD 顶部追加 banner: "本周回声 →"
  · 点击行为:
    1. 通过 obsidian:// URL 跳转到 Weekly/{week}.md
    2. ★ 跳转动作返回成功 → banner 永久隐藏 (持久化到 ~/.keypulse/state.json)
    3. ★ 跳转动作失败 (Obsidian 未启动 / vault 路径错) → banner 不隐藏,告警提示
  · 不点击: banner 持续显示直到下周新 weekly 生成时覆盖

[fallback · 本周日报 < 5 天]
  · 不写 weekly.md
  · HUD 顶部一行轻提示:
    "这周记得不多,要不要补一句这周印象最深的事? [inline input]"
  · 用户填 → 当作下周 weekly 的 hud_inputs 输入
  · 不填 → 不强催
```

---

## 7 · M1 范围 · 待评审

#### M0 前置(必须先做,否则聚类管线没有硬证据可用)

| 项 | M0 做 | 理由 |
|----|-------|------|
| **sources → raw_events 写入断层修复** | ✅ | git/claude_code/codex_cli/markdown_vault/chrome_history 等 source 当前只产 SemanticEvent,不入 raw_events。需补一条统一 sink |
| **raw_events.metadata_json 规约 `entities` 子字段** | ✅ | 约定 `{commit_hash, file_paths[], urls[], session_id, app_bundle_id, ...}`,聚类管线读它(详见 §4.6) |
| **现有 entity_extractor 的输出落到 metadata.entities** | ✅ | 命名实体 H1 当前完全没存,先用现有 regex/启发式产出 |
| **Chrome tab url 在写 raw_events 时统一去 query** | ✅ | 否则 H1 URL 比对会失败 |

M0 上线 = 数据基础就绪。M1 才能开工聚类管线。

#### M1 主体

| 项 | M1 做 | M1 不做 | 理由 |
|----|-------|---------|------|
| 聚类管线(daily) | ✅ | | 周报地基,daily 也直接受益 |
| 聚类管线(weekly) | ✅ | | 主体 |
| Topics/ 数据结构(新 schema) | ✅ | | 必须 |
| Events/ 标题人话化 | ✅ | | 必须 |
| 「片段-」取消 | ✅ | | 必须 |
| Daily「今日主线」回写 | ✅ | | 必须,顺便修当前债 |
| 客观记录模式 | ✅ | | 主体 |
| 探索者模式 | ✅ 可折叠 | | 一起验证 |
| HUD banner 触达 | ✅ | | 否则写完没人看 |
| 周五 23:35 触发 + ≥5 天阈值 + HUD fallback | ✅ | | 必须 |
| Topics/ 空壳 lint 清理 | ✅ 一次性脚本 | | 历史债,做完不再做 |
| Events/Topics 迁移到 `.keypulse/` 隐藏目录 | ✅ migration 脚本 | | 用户视角不见,只看 Daily/Weekly |
| Hot cache (`.keypulse/hot.md`) | ✅ daily 末尾刷新 | | 借鉴 claude-obsidian,加速 weekly |
| 决策日志 (`.keypulse/log.md`) | ✅ append-only | | 借鉴 Karpathy/nashsu,失败可追溯 |
| 用户批注写回长期记忆 | ❌ | | 仅预留空 callout,M2 处理 |
| 跨周对比 | ❌ | | M2 数据攒够再说 |
| 用户追问 | ❌ | | M2 |
| 多语言/多 vault | ❌ | | M2 |

---

## 8 · 风险 · 主动暴露

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| **聚类质量不稳** —— LLM 把不相关的事归一类,或同一件事拆两类 | 高 | (1) 三层信号兜底,LLM 只做未识别部分 (2) Daily 阶段聚类后,用户 Obsidian 编辑可校正 (3) 每周 lint 找异常主题 |
| **AI 味 / 套话** —— 客观记录写出"如火如荼""紧锣密鼓"这种 | 高 | (1) Prompt 词汇黑名单 (2) 自己用 ≥ 4 周再开放给任何人 (3) 字数硬上限(120 字/段)逼 LLM 简洁 |
| **Topics 数量爆炸** —— 每周新建 5-10 个,半年 100+ | 中 | (1) LLM 必须 fuzzy 匹配现有 keywords (2) `status: dormant` 自动归档 (3) Misc 兜底,不强求每条都归类 |
| **HUD inline input 用户不填** —— 探索者模式"没接住"信号空 | 中 | (1) 没填就不写"没接住"段 (2) HUD 不强催,用户自然填 (3) 弱信号 M2 再补(daily 正文里抽待办) |
| **触达失败** —— 周五 23:35 用户在外面没看 HUD | 中 | (1) HUD banner 持续 2 天 (2) M2 加 macOS 通知或邮件 |
| **首次冷启** —— 新用户没历史 Topics,主题信号全靠 LLM 现造 | 低 | 第一周质量必差,第二周开始进入正循环。Onboarding 文档说明 |
| **重构 Obsidian 现有数据** —— 80 张 Topics 卡 + 几百事件卡迁移可能炸 | 高 | (1) 写 migration 脚本 + dry-run (2) 先备份整个 vault (3) 从空 Topics 卡开始,不动有内容的 |

---

## 9 · 还要 Harland 拍的决策点

(已合并到本文档,这是二阶段唯一来源,无单独 PRD)

#### 已定(本轮反馈合并)

| # | 决策点 | 已定 |
|---|--------|------|
| ✅ | 文档落位 | 增量更新本 md,二阶段唯一来源 |
| ✅ | 聚类时序 | **同步**(在 daily 生成流程内),走方案 C 一次 LLM |
| ✅ | Daily 出报时机 | 18:00 首版 + 增量 + 23:30 收尾 |
| ✅ | 探索者是否折叠 | **不折叠**,在 weekly.md 后段独立板块 |
| ✅ | Events/Topics 用户可见性 | **不可见**,迁到 `.keypulse/` 隐藏目录 |
| ✅ | Projects/ 是否纳入 | **不纳入**,用户独立内容 |
| ✅ | 长期主题降权 | **不降权**,改为衰减"对同一主题的同类观察" |
| ✅ | 触达隐藏逻辑 | 点击跳转**成功后**永久隐藏,失败不隐藏 |

#### 已定(轮 3 · Harland 照单全收倾向)

| # | 决策点 | 已定 |
|---|--------|------|
| 1 | Misc 离题事件 | 完全不入 Topic,只在 Daily 当日「## 散点」段记录,过夜不持久化 |
| 2 | 「持续主线」识别 | 自动识别(连续 ≥ 4 周) + 用户 topic frontmatter 加 `pinned: true` 强制 ongoing,两者都支持 |
| 3 | 客观记录 top N | **N = min(8, 非 misc 主题数)**,数据少就少写,不凑数 |
| 4 | Events 历史卡迁移 | **不动历史**,只新事件用新规则;历史卡保留 `id` frontmatter,搜索仍可找到 |
| 5 | Topics 现状清理时机 | **先做**(M1 第一个 PR 单独清理,dry-run 让 Harland 确认,再合并) |
| 6 | 四信号亲和度阈值 | **先 5.0**,M1 上线两周后用 log.md 真实数据校准 |
| 7 | 18:00 触发机制 | **launchd 18:00 + 异步 LLM 调用**(后台跑,不阻塞 HUD) |
| 8 | 方案 C 失败兜底 | retry 2 次失败 → 降级方案 B(先聚类不写 narrative + 第二次 LLM 单独写叙事);再失败 → 纯规则层(只列 H3 + 占位文案) |
| 9 | M0 拆分粒度 | **单独 PR 先合**(PR0),与 PR1 Topics 清理可并行 |

---

## 10 · 评审后下一步

定稿这份方案 →
- 拆 brief 给 Codex MCP(大块实现优先,详见 `docs/weekly-report-codex-prs.md`)
- 分 6 个 PR(顺序):
  - **PR-1 基础设施**(LLM cache + cost tracking + prompt_version + typed JSON schema + daily-summary 中间态 + 模型档位 config)。详见 §11。**所有后续 PR 依赖**
  - **PR0 M0 数据基础**(sources → raw_events 写入 sink + metadata.entities 规约 + entity_extractor 落库 + Chrome url 去 query)。详见 §4.6 / §7。**与 PR1 可并行**
  - **PR1 Topics 现状清理**(空壳 lint + 备份)+ 迁到 `.keypulse/` 隐藏目录 migration 脚本。**与 PR0 可并行**
  - **PR2 聚类管线 daily**(硬证据建图 + 拆分 L1/L2/L3 三次 LLM + hot.md/log.md + Daily「今日主线」回写)。**依赖 PR-1 + PR0**
  - **PR3 Weekly 生成器**(L4 reconcile + L5 客观记录 + L6 探索者 + 渲染)。**依赖 PR2**
  - **PR4 HUD banner 触达** + Events 文件名规范化 + 「片段-」前缀取消
- 自己用 4 周(28 daily + 4 weekly)再决定是否扩展(批注写回 / 跨周对比 / 8 类 Lint)

---

## 11 · 基础设施层(横切关注点)

> 这一节是 §1-10 之外的横切关注点:缓存、token 经济、可持续性。**M1 PR-1 必须做完**,所有后续 PR 才能开工。

### 11.1 三档模型(按规模分,不绑厂商)

| 档 | 规模 | 代表模型 | $/M in | $/M out | 月成本(典型用量) | 质量 |
|---|---|---|---|---|---|---|
| **mini** | 7-8B | Llama 3.1 8B / Qwen 2.5 7B / Phi-4 / 本地 Ollama | $0.05–0.20 / 本地 0 | $0.10–0.40 | $0–0.10 | ~70%(叙事粗糙) |
| **standard** ⭐ | 70-200B | DeepSeek v3 / Qwen Max / GPT-4o-mini | $0.15–0.30 | $0.60–1.10 | **~$0.10**(30% cache) | ~90% |
| **premium** | 200B+/顶配 | Claude Sonnet 4.6 / GPT-4o / Claude Haiku 4.5 | $1–3 | $5–15 | ~$1.50 | ~100% |

#### 已定

- **默认档**:`mini 本地`(Ollama),首次跑通门槛低,月 $0
- **推荐升档**:`standard` 月 ~$0.10,实际用 DeepSeek v3 / Qwen Max
- **README 承诺**:"开 standard 档,月成本 ≤ $1"(实际 10x 缓冲)
- **配置位置**:`~/.keypulse/config.toml` 加 `[llm]` 段,字段 `tier = "mini" | "standard" | "premium"` + `provider`(可选,标准模型由档位映射默认值)
- **小任务自动降档**:L2 Narrative / L3 TopicNaming 即使全局是 standard,也自动调用 mini 档(单聚类/命名是小任务,质量差距小)

### 11.2 12 项 token 节省工程手段(M1 必做清单)

| # | 手段 | 收益 | 实现位置 |
|---|---|---|---|
| 1 | **Input-hash LLM cache**(`.keypulse/cache/llm/{sha256}.json`) | 30-50% 调用免除 | PR-1 |
| 2 | **Topics 索引裁剪**(hot + 关键词命中,硬上限 30 条 × 50 tokens) | input -70% | PR2(已写 §4.3) |
| 3 | **规则层硬证据建图**(LLM 不判"是否聚一起",只裁决) | input -30% | PR2(已写 §4.2) |
| 4 | **空日跳过**(当日事件 < 3 条不调 LLM) | 节流稀疏日 | PR2 |
| 5 | **增量收尾**(23:30 只送 pending) | output -60% | PR2 |
| 6 | **Prompt prefix caching**(prompt 模板部分稳定 → API 自动 50% 折扣) | input -50%(命中部分) | PR-1(client 层) |
| 7 | **小任务降档**(L2/L3 用 mini) | 总成本 -40% | PR-1 |
| 8 | **Output 严限**(`max_tokens` 按段落硬上限) | output -30-50% | PR-1 + PR2 |
| 9 | **失败降级**(retry 2 次失败 → 规则层 + 占位文案,不再调 LLM) | 防跑飞 | PR-1 |
| 10 | **Daily-summary 中间态**(weekly 不 reparse markdown) | weekly input -50% | PR-1 + PR3 |
| 11 | **Topic frontmatter 压缩**(`{slug, name, kw5, last_seen}`) | input -30% | PR2 |
| 12 | **Batch API**(供应商支持时) | -50% 但延迟分钟级 | M2 不在 M1 |

#1, 2, 3, 5, 10 是大头,加起来把月成本压到 §4.3 估算的 **40-50%**。

### 11.3 6 个 LLM 调用点(单体 → 拆分)

```
[原单体]
  events → 硬证据建图 → ★ 1 次 LLM(裁决+叙事+命名) → 渲染

[拆分后(M1 实施)]
  events → 硬证据建图 → ★ L1 ClusterReview(JSON 裁决)
                       → ★ L2 Narrative × N 聚类(并行,markdown 段)
                       → ★ L3 TopicNaming × M 新主题(并行,JSON)
                       → 渲染
                       
  weekly:
  daily-summary × 7 → ★ L4 WeeklyReconcile(JSON)
                    → ★ L5 WeeklyMainNarrative(批量,markdown)│ 并行
                    → ★ L6 Explorer(markdown)                 │
                    → 渲染
```

#### 调用点全表

| # | 名称 | 触发 | 输入 | 输出 | in/out tokens | 模型档 | 缓存 key | 降级 |
|---|---|---|---|---|---|---|---|---|
| **L1** | `ClusterReview` | daily 18:00 + 23:30 | 硬证据连通分量 + merge_candidate + topics 裁剪索引 + HUD 锚点 | typed JSON: 每分量的 `{topic_action, merge_with?, slug?}` | 3000 / 500 | **standard** | sha256(events+topics+anchor) | retry 2 → 全归 misc |
| **L2** | `Narrative` | L1 后并行,每聚类一次 | 单聚类事件 + topic 上下文 | 段落叙事(80-150 字) | 800 / 200 | **mini** | sha256(events+topic) | retry 2 → "本日 N 个相关事件,最早 X,最晚 Y" |
| **L3** | `TopicNaming` | L1 决定开新 topic 时并行 | 该聚类事件 + 现有 slug 集合 | `{slug, display_name, keywords[5-10]}` | 600 / 80 | **mini** | sha256(events+slug_set) | retry 2 → 用首事件 actor+intent 拼 slug |
| **L4** | `WeeklyReconcile` | 周五 23:35 | 本周 merge_candidate_pairs + 亲和度 ≥ 5 的 topic 对 | typed JSON: `{merge_pairs: [{a,b,decision}]}` | 2500 / 300 | **standard** | sha256(pairs+topics) | 跳过 reconcile,各自保留 |
| **L5** | `WeeklyMainNarrative` | L4 后批量,与 L6 并行 | top N 主题的 7 天 entries + 加速度 | 每主题一段周叙事 | 5000 / 1500 | **standard** | sha256(topics+week) | daily-summary 简单拼接 |
| **L6** | `Explorer` | 周五 23:35,与 L5 并行 | 本周事件 + 主题状态 + HUD anchor 历史 | 没接住的球 + 一个观察 | 3500 / 400 | **standard** | sha256(events+anchors+week) | 跳过探索者段 |

### 11.4 月度成本估算(standard 档 + 30% cache 命中)

| 调用 | 月度次数 | 月度 in | 月度 out | 月度成本 |
|---|---|---|---|---|
| L1 | 60 | 126K | 21K | $0.04 |
| L2 | ~150 | 84K(mini 档) | 21K(mini 档) | $0.03 |
| L3 | ~30 | 13K(mini 档) | 1.4K(mini 档) | $0.005 |
| L4 | 4 | 7K | 0.84K | $0.003 |
| L5 | 4 | 14K | 4.2K | $0.008 |
| L6 | 4 | 10K | 1.1K | $0.005 |
| **合计** | **~252 次/月** | **~254K** | **~50K** | **~$0.09** |

mini 档(全云):~$0.02/月。premium 档:~$1.50/月。

### 11.5 缓存与中间态存储(D1.1 选 B)

```
~/.keypulse/
├── cache/
│   └── llm/
│       └── {sha256}.json     # LLM 调用缓存,30 天 TTL
├── daily-summary/
│   └── {date}.json           # 给 weekly 用,永久保留
├── topics/
│   └── {slug}.md             # 主题(权威态)
├── events/
│   └── {date}/{...}.md       # 原子事件
├── hot.md                    # 滑窗活跃主题缓存
├── log.md                    # 决策日志 append-only
└── cost.jsonl                # token 成本记录 append-only
```

#### Cache schema(`cache/llm/{sha256}.json`)

```json
{
  "key": "<sha256(stable_serialize(input))>",
  "input": { "...": "原始 input" },
  "output": "<LLM 原始输出>",
  "ts": "<UTC ISO>",
  "ttl_days": 30,
  "model": "<provider/model_id>",
  "tier": "standard",
  "in_tokens": 3142,
  "out_tokens": 487,
  "cost_usd": 0.0021,
  "prompt_version": "L1.v1",
  "capability": "ClusterReview"
}
```

#### Daily-summary schema(`daily-summary/{date}.json`)

```json
{
  "date": "2026-05-06",
  "clusters": [
    {
      "slug": "keypulse-hud-fix",
      "display_name": "修 HUD 信号源",
      "narrative_one_line": "...",
      "event_count": 5,
      "time_range": ["00:05", "01:32"],
      "merge_candidate_with": []
    }
  ],
  "misc_event_ids": [],
  "topic_status_snapshot": {},
  "cost": { "in_tokens": 4700, "out_tokens": 1500, "cost_usd": 0.0035 }
}
```

#### TTL / 失效策略

| 数据 | 保留 | 失效触发 |
|---|---|---|
| `cache/llm/*.json` | 30 天滚动 | TTL + topics 索引变更时**标 stale**(不立即删,LLM 命中前查 stale flag) |
| `daily-summary/*.json` | **永久** | 不失效(daily 是冻结快照) |
| `cost.jsonl` | 永久 | append-only,从不删 |
| `hot.md` | 滚动覆盖 | 每天 daily 末刷新 |
| `log.md` | 永久 | append-only |

### 11.6 Cost tracking(诚实化)

#### `cost.jsonl` 字段

```json
{"ts":"2026-05-06T18:00:01Z","capability":"L1","model":"deepseek-v3","tier":"standard","in_tokens":3142,"out_tokens":487,"cost_usd":0.00213,"cache_hit":false,"prompt_version":"L1.v1"}
```

#### 用户可见展示

- **HUD 顶栏**:`💰 本月 $0.07`(从 `cost.jsonl` 滑窗算 30 天)
- **Weekly.md 顶部**:渲染时注入 `> 本期生成成本:$0.02 · LLM 调用 12 次(含 4 次 cache 命中)`
- **`keypulse cost` CLI 子命令**:输出本月/本周成本明细 + 按 capability 分组

#### 预算控制

- **软警告**(M1):`config.toml` 设 `monthly_budget_usd = 5.0`,接近时 HUD 黄字 + log 提醒
- **硬上限**(M2):超出后所有 LLM 调用降级到规则层 + 占位文案

### 11.7 可持续性(为 M2 留口子)

#### M1 必做的 4 件"为 M2 演进留口子"

| # | M1 必做 | 为什么 |
|---|---|---|
| 1 | **每个 LLM 调用带 `prompt_version` 字段** | 否则 M2 换 prompt 后无法回归比对 |
| 2 | **L1/L3/L4 输出严格 JSON typed schema**(L2/L5/L6 是叙事用 markdown) | 否则未来加 capability 要重 parse |
| 3 | **`.keypulse/topics/` 写入用 typed diff**(不是全量重写) | 让多 capability 输出能合并 |
| 4 | **`.keypulse/log.md` 记录 capability 名 + decision** | 否则未来调试聚类问题没线索 |

#### M2 目标架构(M1 不实现,但 M1 数据格式不能堵死)

```
Capability 接口:
  input: cluster_set | event_set | topic_set | week_window
  output: typed diff (TopicChange | EventTag | NarrativeBlock)

具体 capability:
  ClusterReviewCapability  (L1, M1 已有)
  NarrativeCapability      (L2, M1 已有)
  TopicMatchCapability     (M2: 当前合并在 L1 里)
  AnnotationCapability     (M2: 用户批注影响下次聚类)
  TrendCapability          (M2: 跨周对比/衰减曲线)
  LintCapability           (M2: 8 类清理)
  QueryCapability          (M2: "上周我在 X 上花多少时间")

调度器(DailyOrchestrator/WeeklyOrchestrator):
  - 决定调哪些 capability、顺序、是否并行
  - 收集 typed diff,统一应用到 .keypulse/topics/
  - 每次调用记 prompt_version + model_id 进 cost.jsonl
```

### 11.8 PR-1 验收清单

PR-1 合并前必须验证:

- [ ] `~/.keypulse/cache/llm/` 目录创建,sha256 命中复用 LLM 输出(单元测试)
- [ ] `~/.keypulse/daily-summary/{date}.json` schema 实现,可被 weekly 路径读取
- [ ] `~/.keypulse/cost.jsonl` 每次 LLM 调用记录,字段完整
- [ ] `keypulse cost` CLI 可输出本月/本周成本
- [ ] `config.toml` `[llm]` 段支持 `tier = mini|standard|premium`,3 档绑定具体 model
- [ ] `tier=mini` 默认走本地 Ollama;若不可用,fallback 提示用户
- [ ] L1-L6 的 prompt_version 字段全部就位(prompt 文件以 `prompts/L1.v1.md` 形式管理)
- [ ] L1/L3/L4 输出 JSON schema 已用 pydantic / jsonschema 验证
- [ ] HUD 顶栏显示"本月 $X.XX"
- [ ] 月度软预算警告可触发
