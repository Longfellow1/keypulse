# KeyPulse 冷启动设计文档

> 范围：用户首次启动 → 画像骨架成型 → 笔友进入"成长期"  
> 阶段定位：数据采集质量优先，不做功能堆砌

---

## 一、冷启动的核心问题

KeyPulse 面临所有"陪伴型产品"共同的冷启动悖论：

- 笔友要了解你才能说话
- 但用户在笔友开口之前，感受不到价值
- 于是用户在笔友学会说话之前就流失了

解法不是缩短学习周期，而是**让用户在等待期感受到"在发生什么"**。  
冷启动设计的目标：让画像骨架的形成过程本身变得有参与感。

---

## 二、整体节奏

```
Day 0   首次启动 → 信任建立 → 身份锚点采集（第一层）
Day 1   项目上下文挂载（第二层）
Day 3   被动采集积累 + 第一次笔友反应（咿呀期开始）
Day 7   思想轨迹引导（第三层）
Day 14  画像骨架成型，笔友进入长成期
```

---

## 三、Day 0：信任建立 + 身份锚点

### 3.1 首次启动界面

不做 onboarding tour，不讲功能。只说一件事：

```
你好，我还不认识你。

在我开口之前，我需要先看一段时间。
你愿意先告诉我你是谁吗？

这些内容只存在你自己的机器上。
永远不会离开这里。
```

一个按钮：**开始认识**  
一个链接：跳过，先让我看着

> 设计意图：让用户主动选择进入，不强迫。选择跳过的用户进入纯被动采集模式，不影响后续引导。

---

### 3.2 身份锚点采集（第一层）

分三步，每步单独一屏，不堆在一起。

**Step 1：你在做什么**

```
你现在主要在做什么？
（一句话就够，不用完整，我会慢慢理解的）

[文本输入框，placeholder: 比如"在做一个 macOS 工具"或"在一家 AI 公司带产品"]
```

这条输入直接写入 `manual_notes`，`speaker: user`，`semantic_weight: 1.0`，打 tag `profile:current_focus`。

---

**Step 2：有没有什么文件可以让我先读一读**

```
有没有介绍你自己的文件？
简历、自我介绍、项目说明——任何能让我快速了解你的东西都行。

[拖拽区域]
支持 PDF、Markdown、TXT、Word

不想给也没关系，我慢慢看。
```

文件处理流程：
```
文件拖入
  → 本地解析（pypdf / python-docx / markdown）
  → 字段脱敏（身份证号、银行卡、手机号）
  → 分段向量化 → 写入 profile_documents 表
  → 提取关键实体（姓名、技能、项目名、公司名）→ 写入 profile_entities 表
  → 不保存原文，只保存向量 + 实体
```

---

**Step 3：我来跟你确认一下我理解的你**

基于 Step 1 输入 + Step 2 文件提取，LLM 生成一段简短的"我对你的第一印象"：

```
基于你告诉我的，我的第一印象是：

你是一个在做 AI 工具的产品人，
现在的重点是一个叫 KeyPulse 的 macOS 项目，
同时还在推进 CorpusFlow。

有什么说错的吗？

[可以直接编辑这段话]  [没问题，继续]
```

用户的编辑本身也是高质量输入，写回 `manual_notes`，打 tag `profile:correction`。

> 这一步的价值不只是校准，是让用户第一次感受到"它在理解我"，建立后续信任基础。

---

## 四、Day 1：项目上下文挂载（第二层）

不在 Day 0 做，避免信息过载。  
Day 1 的每日报告底部出现一个轻提示：

```
你昨天花了 3 小时在 ~/Go/keypulse 目录。
我对这个项目还不太了解，要不要让我读一读？

[挂载项目文档]  [以后再说]
```

触发条件：连续出现超过 2 小时的某个目录或项目名。

---

### 挂载方式

支持三种，按摩擦从低到高排：

```
1. 目录挂载
   keypulse mount ~/Go/keypulse
   → 扫描 README、CHANGELOG、.md 文件、docstring
   → 不读源码，只读文档层

2. 文件手动拖入（同 Day 0）

3. Git 仓库摘要
   keypulse mount --git ~/Go/keypulse
   → 读 commit message 历史
   → 提取项目演进脉络，不读代码内容
```

---

### 处理后写入

```sql
profile_projects (
  name TEXT,           -- "keypulse"
  path TEXT,           -- ~/Go/keypulse
  summary TEXT,        -- LLM 生成的一句话摘要
  tech_stack TEXT,     -- ["Python", "SQLite", "launchd"]
  last_active TEXT,    -- 最近一次 watcher 观察到活跃
  mounted_at TEXT
)
```

挂载完成后，当天的 Obsidian 事件卡开始用项目名替代路径显示。  
这是用户能感受到的第一个"它更懂我了"的具体信号。

---

## 五、Day 3：第一次笔友反应

被动采集满 72 小时后，触发"咿呀期"第一条输出。

生成条件：
- 至少有 1 条身份锚点
- 至少有 50 条 `speaker: user` 的语义事件
- 至少识别出 1 个高频主题

输出位置：当天 Obsidian 报告的"今日主线"之后，新增一栏：

```
## 笔友说

我还在学认识你，但我注意到一件事——
这三天你反复回到 keypulse，
有时候是凌晨，有时候是早上刚起来。

是卡在什么地方了，还是这本来就是你想做的事？

（不用回答我，写给自己也行）
```

> 这条输出的质量是冷启动成功与否的关键验收点。  
> 如果 LLM 生成质量不达标，fallback 策略是**不输出**，而不是输出硬编码文本。  
> 沉默比说错话更符合笔友人设。

---

## 六、Day 7：思想轨迹引导（第三层）

Day 7 报告底部出现：

```
你用了 KeyPulse 一周了。

如果你有历史笔记、读书摘录、或者以前写过的想法——
把它们挂进来，我会更快地理解你在想什么。

[挂载 Obsidian Vault]  [挂载文件夹]  [暂时不用]
```

---

### Obsidian Vault 挂载特殊处理

Obsidian vault 有双链结构，处理时要保留链接关系：

```python
def mount_obsidian_vault(vault_path: str):
    notes = scan_markdown_files(vault_path)
    for note in notes:
        entities = extract_entities(note.content)
        links = extract_wiki_links(note.content)   # [[链接]] 结构
        tags = extract_tags(note.content)          # #标签

        write_profile_note(
            title=note.title,
            entities=entities,
            links=links,           # 保留图结构
            tags=tags,
            created_at=note.created,
            modified_at=note.modified,
            # 不存原文，只存语义摘要
            summary=llm_summarize(note.content),
        )

    # 构建主题图：哪些主题经常共现
    build_topic_cooccurrence_graph(notes)
```

---

## 七、画像骨架数据结构

冷启动完成后，`~/.keypulse/profile.db` 的核心结构：

```sql
-- 身份锚点
profile_identity (
  key TEXT PRIMARY KEY,   -- "current_focus", "name", "role"
  value TEXT,
  confidence REAL,        -- 0-1，用户确认过的为 1.0
  source TEXT,            -- "manual" | "file_extract" | "inferred"
  updated_at TEXT
)

-- 文档库
profile_documents (
  id TEXT PRIMARY KEY,
  title TEXT,
  doc_type TEXT,          -- "resume" | "project" | "note" | "git_history"
  summary TEXT,
  entities TEXT,          -- JSON: [{type, value, weight}]
  vector BLOB,            -- 文档向量，用于检索
  mounted_at TEXT
)

-- 项目上下文
profile_projects (
  name TEXT PRIMARY KEY,
  path TEXT,
  summary TEXT,
  tech_stack TEXT,
  status TEXT,            -- "active" | "paused" | "archived"
  last_active TEXT
)

-- 主题图
profile_topics (
  topic TEXT PRIMARY KEY,
  weight REAL,            -- 出现频率 × 时间衰减
  first_seen TEXT,
  last_seen TEXT,
  cooccurrences TEXT      -- JSON: {topic: weight}
)
```

---

## 八、检索策略：文件 × 主题 × 时间

叙事 pipeline 在构建上下文时，三个维度联合检索：

```python
def build_narrative_context(work_blocks: list, date: str) -> dict:

    # 1. 主题检索：今天出现的主题，找历史相关内容
    active_topics = extract_topics(work_blocks)
    topic_context = search_by_topics(active_topics, limit=5)

    # 2. 文件检索：今天活跃的项目，找挂载的文档摘要
    active_projects = extract_active_projects(work_blocks)
    doc_context = search_by_projects(active_projects)

    # 3. 时间衰减：近期内容权重高，久远内容作为背景
    context = merge_with_decay(
        topic_context,
        doc_context,
        recency_weight=0.7,    # 最近 7 天
        background_weight=0.3  # 更早的内容
    )

    # 4. 身份锚点始终注入，不参与衰减
    context["identity"] = load_profile_identity()

    return context
```

---

## 九、笔友成长期状态机

```
幼儿期（Day 0-2）
  条件：身份锚点已有 / 被动采集 < 72h
  行为：只记录，不输出叙事
  HUD：显示"我在看"

咿呀期（Day 3-13）
  条件：语义事件 > 50 条 / 至少 1 个高频主题
  行为：每日报告出现"笔友说"栏 / HUD 偶发提示
  开口频率：每 2-3 天一次，说一件具体观察到的事

长成期（Day 14+）
  条件：画像骨架成型（identity + 2个以上项目 + 主题图密度 > 阈值）
  行为：每日叙事质量提升 / 开始提出跨天的模式观察
  HUD：开始主动引导输入（"今天你在这件事上花了很多时间，想说两句吗"）
```

状态写入 `health.json`，HUD 根据状态显示对应的表情前缀：

```
🌱  幼儿期
🌿  咿呀期
🌳  长成期
```

---

## 十、冷启动成功指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| Day 0 身份锚点完成率 | > 70% | 至少填写"你在做什么" |
| Day 1 项目挂载率 | > 50% | 至少挂载 1 个项目 |
| Day 3 笔友第一句话质量 | 人工评估 > 7/10 | 不达标宁可不输出 |
| Day 7 留存 | > 60% | 核心健康指标 |
| Day 14 画像骨架成型率 | > 80% | 达到长成期条件 |

---

## 十一、现阶段不做的事

以下功能在数据采集质量稳定之前不碰：

- 周报 / 月报（日报都不稳定，周报是放大问题）
- 跨设备同步（单机先跑通）
- 社交 / 分享功能（与产品定位冲突）
- 复杂的游戏化机制（状态机先跑通，养成感之后叠加）

---

> 当前优先级：输入层语义采集 → 冷启动身份锚点 → 叙事稳定性 → 笔友状态机  
> 顺序不乱，地基先打牢。
