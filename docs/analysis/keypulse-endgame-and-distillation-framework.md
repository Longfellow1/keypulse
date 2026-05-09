# KeyPulse 终局分析：从笔友到方法论操作系统

> 写于 2026-05-08，基于对 KeyPulse 代码库的调研和对 AI 协作知识蒸馏框架的思考。
> 此文档面向人类阅读，也面向 Claude Code / Codex 作为项目蓝图输入。

---

## 一、KeyPulse 现在在做什么

一个五层架构的 macOS 行为采集与叙事系统：

```
采集层 (watchers)
  ├─ 窗口/AX文本/OCR (speaker: system)
  ├─ 键盘段落/剪贴板/手动记录 (speaker: user)
  └─ 摄像头感知暂停 (CMIO 激活时自动停采)
       │
       ▼
隐私层
  ├─ 35 个黑名单应用 (密码管理器/IM/银行)
  ├─ 字段级脱敏 (邮箱/Token/API key/卡号)
  └─ 隐私窗口检测 (Safari/Chrome/Firefox 无痕)
       │
       ▼
存储层 — SQLite (raw_events, sessions, FTS5)
       │
       ▼
Pipeline
  ├─ Session 化 → 工作块聚合 (人机双栏)
  ├─ 主题抽取 (只看 user 事件)
  ├─ 事物提取 (Things — 值得回看的高密度片段)
  ├─ 价值密度计算 (决策信号加权)
  └─ 叙事渲染 (LLM + 确定性兜底)
       │
       ▼
呈现层
  ├─ Obsidian vault (Daily / Events / Topics / Dashboard)
  ├─ 菜单栏 HUD (WKWebView · 今日重点 + 异常修复入口)
  └─ CLI (timeline / search / stats / export)

     ↑ Capability 自检层 (60s 一轮，7 个能力域各自体检)
     ↑ launchd 四件套: daemon / healthcheck / hourly-sync / daily-sync
```

关键设计决策：

- **speaker 模型**：每条事件标注 `user` 或 `system`，日报分两栏渲染，"你的声音始终是主线"
- **隐私写在架构里**：不记键盘输入（只记段落边界），黑名单在写盘前就丢掉
- **出问题给修复路径**：黄条 + 按钮直达系统设置，不是抛错误码
- **llm_mode: "local-first"**：没配模型也能起，daemon 照常采集

已集成的数据源（`pipeline/things.py` L24-35）：

```python
"codex_cli": "Codex",
"claude_code": "Claude",
"zsh_history": "Terminal",
"chrome_history": "Chrome",
"markdown_vault": "Obsidian",
"git_log": "Git",
# ... 等
```

---

## 二、终局：个人方法论图书馆

### 2.1 从 Readme 中提取的终局描述

Readme 第 218-238 行的原文是最精确的终局声明：

> Obsidian 不只是一个笔记本，它是一张图。当日报、主题卡、事件卡不断累积、互相链接，它们慢慢构成的不是「你做过什么」，而是「你是怎么工作的」。KeyPulse 未来会坐在这张图的上面，像一个有耐心的读者 —— 注意那些反复出现的边、提出关于"你怎么做事"的小假设，然后邀请你去命名它们。

> 方向上，有点像 Karpathy 提过的 auto-research，但不是"让模型替你做研究"，而是"让模型给你提出关于你自己的小问题，由你来回答"。时间久了，你的答案本身，就成了产出。

### 2.2 终局的产品形态

| 阶段 | 产出 | 粒度 |
|------|------|------|
| 现在 | 日报 (Daily) | 天 |
| 短期 | 主题卡 (Topics) + 事物卡 (Events) | 天/周 |
| 中期 | 跨日模式识别 ("这周五次会话都反复打开同一个文件") | 周/月 |
| 终局 | 被命名的**个人方法论文件** ("这是你深度工作的热身仪式") | 持久 |

三个具体的终局行为示例（来自 Readme）：

| 观察到的模式 | KeyPulse 的提问 | 产出的方法论 |
|-------------|----------------|-------------|
| 这周五次会话反复打开同一个文件 | "这更像是你思考的地方，而不是你改代码的地方" | 工作流里一个被命名的位置 |
| 每次都是 docs → 终端 → 编辑器 的顺序 | "这可能是你进入深度工作的热身仪式" | 一个可以保留、打破、或研究的模式 |
| 每次 debug 结束都复制一段到小本本 | "你好像在把 fix 提炼成 lesson —— 要不要为这一类给一个方法论文件？" | 属于自己的方法论库，肉眼可见 |

### 2.3 终局的核心机制

```
行为数据 → 模式发现 → 假设提出 → 用户命名 → 方法论入库 → 反哺行为
    ↑                                                              │
    └──────────────────── 循环持续 ─────────────────────────────────┘
```

关键洞察：**命名权始终在人手里**。KeyPulse 只提假设（"你是不是在..."），不做判断（"你应该..."）。这和 Readme 里的产品哲学一致：

1. 这个设计是在**增加关系**，还是只在增加**功能**？
2. 这段文字像**笔友**，还是像**管家**？
3. 用户看到会觉得"它懂我"，还是"它在记账"？

---

## 三、AI 协作知识蒸馏框架（完整设计）

### 3.1 框架总览

蒸馏框架是方法论操作系统的"面朝 AI 工具"那一侧。它的核心命题是：

> 每次你跟 CC/Codex 的交互产生的纠正、踩坑、模式发现，不应该只在当次会话有效。它们应该被捕捉、提取、归纳，变成下次会话时 CC/Codex 启动即可用的知识。

四层蒸馏管线：

```
┌─────────────────────────────────────────────────────────┐
│  L0: 捕捉层 (Capture)                                    │
│  来源：KeyPulse raw_events + 手动 session-notes.md       │
│  粒度：原始事件 / 原始笔记                                │
│  保留期限：30 天（之后归档到 wiki）                        │
├─────────────────────────────────────────────────────────┤
│  L1: 提取层 (Extract → memory.md)                       │
│  粒度：一句一事实                                         │
│  格式：项目级约定、环境事实、用户偏好                       │
│  更新频率：每次 CC/Codex 会话后手动追加，或 cron 周度自动   │
├─────────────────────────────────────────────────────────┤
│  L2: 归纳层 (Synthesize → skills/)                      │
│  粒度：可复用工作流                                       │
│  格式：触发条件 + 步骤 + 陷阱 + 验证清单                   │
│  创建阈值：同一模式出现 3 次以上，或 KeyPulse 跨日模式命中  │
├─────────────────────────────────────────────────────────┤
│  L3: 归档层 (Archive → wiki/)                           │
│  粒度：深度分析、架构决策、技术调研                         │
│  格式：LLM Wiki 格式 (YAML frontmatter + wikilinks)     │
│  触发：架构决策记录、源码阅读笔记、技术选型对比              │
├─────────────────────────────────────────────────────────┤
│  反哺层 (Inject → CLAUDE.md)                             │
│  CLAUDE.md 作为路由中心，引用以上各层                       │
│  CC/Codex 启动时自动加载                                   │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Hermes Agent 的参考实现

Hermes Agent（Nous Research 开源的 AI agent 框架）在自我改进机制上做了最成熟的实现。以下逐层引用其设计，作为蒸馏框架各层的参考。

#### 3.2.1 L1 参考：Hermes Memory 系统

Hermes 的 memory 不是存整个对话，而是存**稳定的命题**。每次修正、每次偏好声明、每次环境发现，存成一句话事实。下次会话开始，所有 memory 注入系统提示，Agent 无需重新学习。

```
设计要点：
- 粒度：一句一个独立事实，不是一段话
- 类型：user profile（用户偏好）和 memory（环境/项目事实）分离
- 操作：add / replace / remove，支持增量更新不覆盖
- 注入时机：每次会话启动，作为系统提示的一部分
- 示例条目：
  "用户偏好 pydantic v2 风格，不用 v1 的 dict() 方法"
  "项目 SQLAlchemy 2.0 async，session 生命周期 request-scoped"
  "所有外部 API 调用必须有 5s timeout 和 retry=3"
```

蒸馏框架的 L1 直接对标这个设计。文件格式：

```markdown
# Project Memory — KeyPulse

> 本文件由蒸馏框架维护。一句一行。新事实追加到对应小节末尾。
> 每次 CC/Codex 启动时，CLAUDE.md 引用此文件。

## 技术栈约定
- SQLAlchemy 2.0 async 模式，session 生命周期严格 request-scoped
- Pydantic v2 风格，用 model_validate() 不用 parse_obj()
- pytest 配置在 pyproject.toml，markers: slow/integration/unit
- 日志通过 keypulse.utils.logging.get_logger()，不直接用 logging.getLogger()

## API / 外部依赖
- LLM 后端通过 keypulse.pipeline.model.ModelGateway 统一调用
- Obsidian vault 路径由 config.obsidian.vault_path 决定，默认 ~/Go/Knowledge
- 所有外部 HTTP 调用必须有 timeout=20s 和 try/except

## 用户偏好
- 代码注释用中文，docstring 用英文
- 测试文件放在 tests/ 对应子目录，命名 test_<module>.py
- PR 前跑 scripts/run_tests.sh，不是直接 pytest
- 不引入新依赖除非在 PR 描述里说明理由
```

#### 3.2.2 L2 参考：Hermes Skills 系统

这是蒸馏框架最关键的一层。Hermes 的 skill 系统设计非常成熟，直接参考：

**Skill 文件格式 (SKILL.md)：**

```yaml
---
name: skill-name                    # lowercase-hyphens, ≤64 chars
description: Use when <trigger>. <one-line behavior>.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [short, descriptive, tags]
    related_skills: [other-skill, another]
---

# Skill Title

## Overview
一句话说明这个 skill 解决什么问题。

## When to Use
- 触发条件 1
- 触发条件 2
- Don't use for: 反例

## Steps
1. 第一步，带精确命令
2. 第二步
3. ...

## Common Pitfalls
1. 陷阱和修复方式
2. ...

## Verification Checklist
- [ ] 验证项 1
- [ ] 验证项 2
```

**Hermes 的 skill 加载机制：**
- 用户主动加载：`/skill <name>` 或 `hermes -s <name>`
- 自动匹配：skill 的 `description` 字段描述触发条件，Agent 在相关场景自动加载
- Skill 内容注入为**用户消息**（不是系统提示），以保护 prompt caching
- 已加载的 skill 在同一会话中保持活跃

**Hermes 的 skill 创建机制：**
- Agent 在完成复杂任务（5+ tool calls）、克服错误、或发现新工作流后，可以**主动保存为 skill**
- 使用 `skill_manage(action='create', name=..., content=...)` 
- Skills 存储在 `~/.hermes/skills/<category>/<name>/SKILL.md`
- 支持 references/、templates/、scripts/ 子目录存放辅助文件

蒸馏框架的 L2 完全采用这个格式，放在项目目录下：

```
~/.claude/skills/
├── python-patterns.md        # Python 编码模式
├── testing-workflow.md       # 测试工作流
├── debugging-postgres.md     # 数据库调试
├── llm-integration.md        # LLM 调用模式
├── code-review-checklist.md  # Code Review 清单
└── deployment.md             # 部署流程
```

示例 skill —— `testing-workflow.md`：

```markdown
---
name: testing-workflow
description: Use when writing, running, or fixing tests. Project-specific pytest conventions and pitfalls.
version: 1.0.0
metadata:
  tags: [testing, pytest, quality]
  related_skills: [python-patterns]
---

# Testing Workflow

## Overview
KeyPulse 项目用 pytest，800+ tests，有 golden set 基线。这个 skill 确保测试相关的操作符合项目约定。

## When to Use
- 用户要求写测试、跑测试、修测试
- 修改了 pipeline/ 或 capture/ 的代码
- Don't use for: 纯文档修改、配置文件修改

## Steps
1. 先读取 pyproject.toml 确认 pytest 配置（markers, asyncio mode, test paths）
2. 新增测试放在 tests/ 对应子目录，文件命名 `test_<module>.py`
3. 运行测试用 `python -m pytest tests/<path> -q`，不用裸 `pytest`
4. 全量跑用 `scripts/run_tests.sh`（会处理环境变量隔离）
5. 涉及 pipeline 叙事渲染的改动，运行 golden set 验证：`python -m pytest tests/golden/ -q`

## Common Pitfalls
1. **不要直接调用 pytest**：`scripts/run_tests.sh` 会设置环境变量隔离，直接跑可能用错配置
2. **Golden set 是基线**：修改 pipeline 阈值时必须跑 golden set，防止静默退化
3. **capability 测试**：涉及 app.py 变更时，需确认 capability 框架的架构不变量测试通过
4. **不要在测试里硬编码路径**：用 `tmp_path` fixture，不引用 `~/.keypulse/`

## Verification Checklist
- [ ] `python -m pytest tests/ -q` 全绿
- [ ] 涉及 pipeline 改动时 golden set 通过
- [ ] 新增测试覆盖 happy path + 边界条件
```

#### 3.2.3 L3 参考：Hermes Session Search + LLM Wiki

Hermes 有两层知识检索机制：

**Session Search（会话搜索）：**
- SQLite FTS5 全文搜索引擎，索引所有历史会话
- 支持关键词搜索、布尔表达式、角色过滤
- 返回匹配会话的 LLM 生成摘要，而非原始对话
- 用途：跨会话记忆检索，"我们上次怎么修的那个 bug？"

**LLM Wiki（知识库）：**
- Karpathy 提出的 wiki 模式：interlinked markdown 文件
- 三层架构：raw/（不可变源材料）→ entities/concepts/comparisons/queries/（Agent 维护的 wiki 页）→ SCHEMA.md + index.md + log.md（导航系统）
- YAML frontmatter 标注创建日期、标签、来源、置信度
- Wikilinks (`[[page-name]]`) 连接知识节点
- 旋转日志：log.md 超过 500 条时归档为 `log-YYYY.md`

蒸馏框架的 L3 采用 LLM Wiki 格式：

```
~/wiki/
├── SCHEMA.md              # 领域定义 + 标签分类 + 编写约定
├── index.md               # 内容目录，每个条目一行摘要
├── log.md                 # 追加式操作日志
├── raw/                   # 不可变源材料
│   ├── articles/          # 网页文章、剪报
│   ├── papers/            # 论文、PDF
│   └── transcripts/       # 会议记录、访谈
├── entities/              # 实体页（人、组织、产品、模型）
├── concepts/              # 概念/主题页
├── comparisons/           # 对比分析
└── queries/               # 值得保存的查询结果
```

关键区别：wiki 存的是**深度分析**（为什么选 FastAPI 不选 Litestar，某次性能优化的完整推导）。它不存**操作流程**（那归 skills/），也不存**项目事实**（那归 memory.md）。

#### 3.2.4 自动化参考：Hermes Cron 系统

Hermes 的 cron 系统可以定时触发蒸馏任务。关键设计：

- **调度粒度**：支持 `30m`、`every 2h`、`0 9 * * *` 等
- **自包含 prompt**：cron 任务在独立会话中运行，无上下文继承，所以 prompt 必须完整
- **交付**：自动发送到指定平台（Telegram/Discord/CLI）
- **Skills 注入**：可以在 cron 任务中自动预加载指定 skills
- **工具集限制**：可以限制 cron 任务可用的工具（如只给 `file` + `web` + `terminal`）

蒸馏框架可以用 Hermes cron 实现自动化蒸馏：

```bash
hermes cron create "every sunday 9pm" \
  --name "distill-weekly" \
  --skills "llm-wiki,obsidian" \
  --prompt "读取 ~/.claude/capture/session-notes.md 中本周的笔记。
提取新的项目级事实更新到 ~/.claude/memory.md（只追加不覆盖，按技术栈/API/偏好分类）。
识别出现 3 次以上的工作流模式，候选创建 skill 文件到 ~/.claude/skills/（用 SKILL.md 格式）。
把架构决策和分析性内容归档到 ~/wiki/ 对应目录。
更新 wiki/index.md 和 wiki/log.md。
输出具体做了什么改动。" \
  --enabled-toolsets "file,terminal,skills"
```

### 3.3 蒸馏框架与 KeyPulse 的映射和互补

| 蒸馏框架的层 | KeyPulse 的对应 | 状态 | 谁负责 |
|-------------|----------------|------|--------|
| L0 捕捉 | `raw_events` SQLite + watchers | **已实现**，daemon 常驻自动化 | KeyPulse |
| L1 提取 (memory.md) | 主题卡 (Topics) + 事物 (Things) | **雏形存在**，缺跨日聚合和项目上下文 | 蒸馏框架（可 cron） |
| L2 归纳 (skills/) | 终局的"方法论文件" | **未实现**，是下一步 | 蒸馏框架 + KeyPulse 模式发现 |
| L3 归档 (wiki/) | Obsidian 图本身 | **存在**（vault 作为阅读面） | 蒸馏框架增强 |
| 反哺 | CLAUDE.md | **未接入** | 蒸馏框架 |

### 3.4 完整目录结构

CC/Codex 启动时读取 CLAUDE.md，CLAUDE.md 作为路由中心引用所有知识层：

```
项目根目录/
├── CLAUDE.md                   ← CC/Codex 启动必读，路由中心
│
├── .claude/                    ← 蒸馏框架产物，面向 AI 工具
│   ├── memory.md               ← L1: 项目记忆（一句一事实）
│   └── skills/                 ← L2: 可复用工作流
│       ├── testing-workflow.md
│       ├── python-patterns.md
│       ├── debugging-postgres.md
│       └── ...
│
├── .claude/capture/            ← L0: 原始捕捉
│   └── session-notes.md        ← 手动 30 秒追加，cron 周度消费
│
├── wiki/                       ← L3: 深度知识库 (LLM Wiki 格式)
│   ├── SCHEMA.md
│   ├── index.md
│   ├── log.md
│   ├── raw/
│   ├── entities/
│   ├── concepts/
│   └── comparisons/
│
└── ~/.keypulse/                ← KeyPulse 数据（面向人）
    ├── keypulse.db             ← raw_events, sessions, FTS5
    └── config.toml
```

### 3.5 CLAUDE.md 作为路由中心的模板

这个模板可以直接给 CC/Codex 启动用。它不做详细指示（那属于 skills/），只做路由：

```markdown
# CLAUDE.md

## 你是谁
[此处放用户身份和工作原则，已从用户现有的 CLAUDE.md 省略]

## 知识系统
本项目有四个知识层，在相关场景下主动读取：

### 项目记忆 (~/.claude/memory.md)
技术栈约定、API 细节、用户偏好。一句话一条。每次启动必读。
当你不确定某个约定时，先查这个文件再动手。

### 技能库 (~/.claude/skills/)
可复用工作流。当遇到以下场景时，主动读取对应文件：
- 写/跑/修测试 → read ~/.claude/skills/testing-workflow.md
- Python 编码模式 → read ~/.claude/skills/python-patterns.md
- 数据库调试 → read ~/.claude/skills/debugging-postgres.md
- [新增 skill 时在这里加一行路由规则]

### 深度知识库 (~/wiki/)
架构决策、技术调研、源码阅读笔记。当你需要理解"为什么这样做"时读取。
入口: read ~/wiki/index.md 找到相关页面。

### 会话捕捉 (~/.claude/capture/session-notes.md)
最近会话的原始笔记。包含踩坑记录和新发现。每次启动浏览最近 30 行。

## 知识反哺
每次会话结束后，如果发现了：
- 新的项目约定 → 追加到 ~/.claude/memory.md
- 可复用的工作流 → 写入 ~/.claude/skills/<name>.md
- 架构决策或深度分析 → 归档到 ~/wiki/
```

### 3.6 端到端工作流示例

以一个完整的周期演示 KeyPulse + 蒸馏框架的协同：

**Day 1-3：KeyPulse 采集**

```
KeyPulse 记录：
- Day 1: 14:23 打开 CC → 14:25 切到 Obsidian 写了一段 → 14:30 切回 CC → 15:10 关闭 CC
- Day 2: 10:05 打开 CC → 10:07 切 Obsidian → 10:15 切回 CC → 11:00 关闭 CC
- Day 3: 09:30 打开 CC → 09:32 切 Obsidian → 09:45 切回 CC → 10:20 关闭 CC
```

**Day 4：KeyPulse 跨日模式发现**

KeyPulse 日报附言：
> "我注意到这三天你每次开 CC 之前，都会先在 Obsidian 里写点什么。你好像在让 AI 帮忙之前，会先自己把问题想清楚。这是你刻意保持的习惯吗？要不要给它一个名字？"

**Day 4：用户命名 + 蒸馏框架执行**

你回复："对，我叫它「理清再问」。我发现每次自己在 Obsidian 里写清楚要什么之后，CC 的第一轮输出质量明显更高。"

蒸馏框架（手动或 cron）执行：

1. **L1 提取 → memory.md 追加：**
```
- 用户偏好：使用 CC 前先在 Obsidian 写清楚需求，提高首轮输出质量
```

2. **L2 归纳 → skills/think-before-asking.md 创建：**
```markdown
---
name: think-before-asking
description: Use when starting a new task with Claude Code or Codex. The user prefers to clarify their thinking before engaging AI.
---

# Think Before Asking

## Overview
用户发现：在用 CC/Codex 之前先在 Obsidian 写清楚需求，AI 的首轮输出质量显著更高。
这个 skill 确保 AI 在用户跳过这一步时温和提醒。

## When to Use
- 用户开始一个新任务，但没有提供足够的上下文
- 用户的问题比较模糊（"帮我修一下这个 bug" 但没有指明是什么 bug）

## Steps
1. 如果你收到的需求描述少于 3 句话，先问：
   "要不要先在 Obsidian 里理一下思路？给我 3 个关键点就行。"
2. 如果用户给了详细的 Obsidian 内容，先复述一遍你的理解确认无误再动手
3. 不要在没有清晰输入的情况下猜测用户的意图

## Common Pitfalls
1. 不要在用户跳过这一步时直接拒绝 —— 温和提醒即可
2. 不要假设用户每次都会提供详细输入 —— 有些小任务不需要
```

3. **L3 归档 → wiki/concepts/clarify-before-asking.md：**
分析这个模式为什么有效（"减少 AI 的搜索空间"、"用户理清思路后 prompt 更精确"、"Cardinality reduction"等）

4. **反哺 → CLAUDE.md 追加路由：**
```
- 用户偏好先理清再求助 → read ~/.claude/skills/think-before-asking.md
```

**Day 5+：循环效果**

- CC 启动时读到 think-before-asking.md
- 你某次急匆匆丢给 CC 一个模糊需求
- CC 回复："要不要先在 Obsidian 里理一下思路？给我 3 个关键点就行。"
- 你意识到自己跳过了热身 → 回到 Obsidian 写清楚 → CC 首轮高质量输出
- KeyPulse 在当晚日报记录：AI 协作流畅度 ↑

这就完成了一个完整的 **观察 → 发现 → 命名 → 入库 → 反哺 → 验证** 循环。

---

## 四、两者的互补关系（架构图）

**KeyPulse 是传感器阵列，蒸馏框架是精炼厂。**

```
                    ┌──────────────────────────┐
                    │    KeyPulse (传感器阵列)    │
                    │                          │
                    │  观察人在做什么            │
                    │  观察人怎么用 CC/Codex     │
                    │  发现行为模式              │
                    │  提出关于"怎么做事"的假设   │
                    └──────────┬───────────────┘
                               │
                               │ 模式 + 假设
                               ▼
                    ┌──────────────────────────┐
                    │   蒸馏框架 (精炼厂)         │
                    │                          │
                    │  L1: 模式 → memory.md     │
                    │  L2: 工作流 → skills/     │
                    │  L3: 深度分析 → wiki/     │
                    │  注入 CLAUDE.md 反哺工具    │
                    └──────────┬───────────────┘
                               │
                               │ 更新 CLAUDE.md + skills/
                               ▼
                    ┌──────────────────────────┐
                    │   CC / Codex (更聪明了)    │
                    │                          │
                    │  启动时读 CLAUDE.md        │
                    │  按需读 skills/           │
                    │  知道项目约定 (memory.md)  │
                    │  知道你的协作偏好          │
                    │  不再犯已知的错误          │
                    └──────────┬───────────────┘
                               │
                               │ 更好的协作 →
                               │ 更流畅的工作 →
                               │ KeyPulse 观察到变化
                               │
                               └──────→ 回到顶部，循环
```

---

## 五、终局的完整图景：方法论操作系统

把 KeyPulse + 蒸馏框架放在一起看，它们在 build 的是同一个东西的不同面：

```
                    方法论操作系统
                          │
          ┌───────────────┴───────────────┐
          │                               │
    面朝人 (KeyPulse)              面朝 AI 工具 (蒸馏框架)
          │                               │
    "我是怎么工作的？"            "怎么让 AI 配合我的方式？"
          │                               │
    观察 → 发现 → 提问          捕捉 → 提取 → 归纳 → 反哺
          │                               │
    产出：自我认知                产出：CLAUDE.md + skills/
          │                               │
          └───────────────┬───────────────┘
                          │
                    方法论库 (Obsidian + .claude/)
                    ├─ 日报 (Daily)               ← KeyPulse 产出
                    ├─ 主题卡 (Topics)             ← KeyPulse 产出
                    ├─ 事物卡 (Events)             ← KeyPulse 产出
                    ├─ 方法论文件 (methods/)        ← 终局产出
                    ├─ 项目记忆 (memory.md)         ← 蒸馏框架 L1
                    ├─ 技能流程 (skills/)           ← 蒸馏框架 L2
                    └─ 深度知识 (wiki/)             ← 蒸馏框架 L3
                          │
                    可阅读、可检索、可分享
                    由你命名、由你塑造、和你一起长
```

这个系统的独特之处：

1. **不是通用效率 tips** — 是你的。从你自己的数据里长出来的。
2. **命名权在人手里** — AI 提假设，你来命名和确认。
3. **双向循环** — 人优化 AI，AI 反过来让人更看清自己。
4. **持久积累** — 每一天的观察都在让整个库更厚、更准。
5. **可分享但不泄露** — 方法论可以给别人看，原始数据永远在本地。

对比现有工具的定位：

| | 仪表盘类 | 量化自我 | AI 助理 | KeyPulse 终局 |
|---|---|---|---|---|
| 记录什么 | 指标 | 数字 | 你的任务 | 你的模式 |
| 反馈方式 | 图表 | 分数 | 替你做事 | 提假设 |
| 产出 | 报告 | 排行榜 | 已完成任务 | 方法论库 |
| 关系 | 你在看它 | 它在评判你 | 它在替代你 | 它在陪伴你 |
| 积累性 | 看完即弃 | 数字堆叠 | 任务完成 | 知识复合增长 |

---

## 六、从现状到终局的路径

### 6.1 已具备的能力

- [x] 行为采集 (daemon + 7 watchers)
- [x] 隐私保护 (黑名单 + 脱敏 + 摄像头暂停)
- [x] 单日数据处理 (session 化 → 主题 → 叙事)
- [x] Obsidian 输出 (日报 + 事件卡 + 主题卡)
- [x] 能力自检 (7 个能力域，60s 一轮，黄条 + 修复按钮)
- [x] 测试覆盖 (800+ tests, golden set)
- [x] CC/Codex 作为数据源 (已集成在 things.py)

### 6.2 尚需建设的能力

按优先级排序：

**P0 — 蒸馏框架的基础设施落地**

不依赖 KeyPulse 的新功能。纯手动 + 纯文件。本周就能做。

- [ ] 在项目根目录创建 `.claude/` 目录结构（memory.md + skills/ + capture/）
- [ ] 从现有 CLAUDE.md 中提取项目级约定写入 memory.md
- [ ] 识别 2-3 个已反复出现的工作流，写为 skill 文件
- [ ] 在 CLAUDE.md 中加入知识系统路由段
- [ ] 开始写 session-notes.md（每次 CC/Codex 会话后 30 秒追加）

**P1 — 跨日模式识别引擎（KeyPulse 侧）**

- 当前只做单日聚合，不做跨日比较
- 需要：检测"反复出现"的模式（同一个文件、同一个 App 切换序列、同一个时间段的类似行为）
- 技术方案：在 `raw_events` 上做滑动窗口 + 频繁模式挖掘
- 输入：`raw_events` 中的 App 切换序列、主题标签、文件路径
- 输出：候选模式列表（带出现次数、跨天数、置信度）

**P2 — 假设生成与提问（KeyPulse 侧）**

- 模式识别出来后，需要一个"翻译层"把模式变成人话提问
- "这周五次都...是不是...？"的生成逻辑
- 可以用 LLM（local-first 模式下用本地模型），也可以规则+模板兜底

**P3 — 方法论文件系统（KeyPulse 侧）**

- 当用户确认一个方法论命名后，创建 `methods/<name>.md`
- 包含：观察到的模式证据、用户的命名和描述、相关的日报链接
- 用 Obsidian wikilink 连接到 Daily/Events/Topics

**P4 — KeyPulse → 蒸馏框架自动桥接**

- KeyPulse 产出的方法论自动同步到项目的 CLAUDE.md（或 .claude/skills/）
- 实现蒸馏框架的反哺链路自动化
- 可能的实现：方法论确认后，触发 hook 写 `.claude/skills/<method>.md`

**P5 — 方法论库的分享机制**

- 导出选定的方法论文件（不含原始事件数据）
- 生成可分享的 markdown 或静态页面

### 6.3 风险与难点

1. **冷启动问题**：模式识别需要足够的数据量。新用户的前 2-4 周可能只能看到日报，看不到跨日模式。
   - 缓解：明确告知用户"我在积累素材"，用天数作为进度指示
   - P0 蒸馏框架不依赖 KeyPulse 数据量，手工即可启动

2. **假阳性模式**：频繁模式挖掘会产生噪音。一次碰巧的行为序列不是方法论。
   - 缓解：设置置信度阈值（至少出现 N 次、跨 M 天），并始终让用户确认

3. **隐私与模式发现的矛盾**：越好的模式识别需要越多的数据。但 KeyPulse 的隐私承诺限制了数据粒度。
   - 缓解：模式识别只看聚合信号（App 切换序列、时间段分布、主题标签），不看原始内容

4. **笔友语气的一致性**：当 KeyPulse 从"聊今天"变成"聊你的模式"时，语气不能滑向"管家汇报"。
   - 缓解：所有假设性提问走同一个产品哲学的三条红线检查

5. **知识腐烂**：skills/ 和 memory.md 需要维护。项目演进后过时的约定会产生误导。
   - 缓解：每次修改 memory.md 时标注日期。Cron 任务可以做"memory/skill 审计"，标记 90 天未更新的条目供人工 review

---

## 七、立即可以做的事（CC 接棒清单）

以下是不需要 KeyPulse 新功能、不需要 cron、今天就能让 CC 执行的任务：

### Task 1: 创建蒸馏框架目录结构

```
在 keypulse 项目根目录下创建：
  .claude/memory.md
  .claude/skills/testing-workflow.md
  .claude/skills/python-patterns.md
  .claude/capture/session-notes.md

并从现有代码和 CLAUDE.md 中提取初始内容填充 memory.md。
```

### Task 2: 扩写 CLAUDE.md 的知识系统路由段

```
在现有 CLAUDE.md 中追加"知识系统"段（参考本文 3.5 节的模板）。
注意：不要覆盖现有的"你是谁""工作原则""组队模式"等内容。
只追加知识路由 + 知识反哺两个段。
```

### Task 3: 识别已有工作流编写 skill

```
基于 keypulse 代码库的已有模式，编写至少 2 个 skill：
1. testing-workflow.md — 基于 scripts/run_tests.sh + golden set
2. python-patterns.md — 基于 pydantic config 模式、logger 约定、observer 模式等
```

### Task 4: 提取项目记忆

```
扫描 keypulse 代码库中的约定和模式，填入 memory.md：
- 技术栈约定（SQLAlchemy async、Pydantic v2、pytest 配置）
- API/外部依赖约定（ModelGateway、Obsidian vault 路径、timeout 约定）
- 代码风格（注释中文/docstring英文、测试命名、依赖管理）
- 用户偏好（从项目设置推断）
```

---

## 八、总结

KeyPulse 的终局不是"一个更好的日记应用"，而是：

**一个从你日常行为数据中生长出来的个人方法论图书馆。它观察你是如何工作的，提出关于你的工作方式的假设，邀请你来命名和确认，最终形成一套只有你才有的、但可以分享给他人的工作方法。**

蒸馏框架是这个操作系统的另一面：**把方法论变成 AI 工具可读取、可执行的指令，让 CC/Codex 每次启动都带着你上一次教它的东西回来。**

两者在 Obsidian 的知识图上汇合，形成「人 ↔ 方法论 ↔ AI 工具」的持续进化循环。

Hermes Agent 为这个系统提供了最成熟的参考实现：Memory（一句一事实的持久记忆）、Skills（触发式可复用工作流）、Session Search（FTS5 全文检索历史）、LLM Wiki（复合增长的知识库）、Cron（定时自动化蒸馏）。蒸馏框架的每一层都有对应的 Hermes 设计可以借鉴。

---

## 九、路线图：开源策略与商业化路径

### 9.1 基因约束：什么不能做

在做路线图之前，先列负面清单。这些东西跟 KeyPulse 的 DNA 直接冲突——违了基因，赚到钱也活不久。

**不能是 SaaS。**
Readme 第 57 行："本地、可审计、在你自己机器上。不是收割你注意力数据的 SaaS。" 一旦数据离开用户机器，信任就碎了。这不是功能取舍，是存在论级别的约束。KeyPulse 的隐私承诺不是 marketing，是架构。打破它，产品就没有存在的理由。

**不能按"效率工具"定价。**
KeyPulse 拒绝"仪表盘"和"量化自我"定位。按 Dashboard 模式收费（$X/月看报表）等于背叛产品哲学。用户不是来买一份报告的——报告只是表层，方法论才是底层价值。定价应该围绕"方法论发现和命名"的能力，而不是"看数据"的能力。

**B 端不能卖"员工监控"。**
虽然技术上有采集能力，但笔友人设一旦沾监控，用户关系立即变成敌对关系。"在陪伴你"和"在监视你"之间有一道墙，跨过去就回不来。团队版的价值主张必须是"自愿共享方法论"，不是"管理者看报表"。

### 9.2 三阶段路线图

#### Phase 1：现在 → 稳定个人工具（0→1 用户价值）

**目标**：一个人用起来觉得"这东西不能没有"。不做增长，不做推广，只做深度。

**关键里程碑：**

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| M1 | 蒸馏框架基础落地 | 完成第七节 4 个 Task。CLAUDE.md + skills/ + memory.md 就位，CC 启动时读取 |
| M2 | 跨日模式识别 MVP | 最简版本：同一文件 N 天内被打开 M 次以上 → 标记"反复回到的地方" |
| M3 | 第一方法论产出 | 用户成功命名至少 1 个方法论（如"理清再问"、"先搜再挖"、"终端-编辑器-浏览器三角"） |
| M4 | 蒸馏自动化 V0 | Hermes cron 或 launchd job 做周度 session-notes 蒸馏。memory.md 和 skills/ 开始自己长 |

**这一阶段不开源、不推广。** 就是你和几个信任的朋友用。把东西做对自己，比做给很多人重要。方法论操作系统的内核必须在自己身上先跑通。

#### Phase 2：开源 + 社区建设（1→N 用户价值）

**目标**：让"个人方法论"成为一个被认可的品类。KeyPulse 不是"又一个活动追踪器"，而是"方法论操作系统的第一个实现"。

**开源策略：**

```
开源什么（Apache 2.0）：
├─ keypulse-core      — 采集 + Pipeline + Obsidian 输出
├─ keypulse-cli       — timeline / search / stats / export
├─ 蒸馏框架规范        — SKILL.md 格式 / memory.md 格式 / CLAUDE.md 路由模板（CC0）
└─ 方法论文件示范集     — 你命名的前 10 个方法论，脱敏后公开（CC0）

不开源什么：
├─ HUD GUI            — 菜单栏小窗，作为差异化保留
├─ 跨设备 merge 服务   — 如果有
└─ 云同步层           — 如果有
```

**为什么这个阶段必须开源：**

1. **信任需要透明。** 一个说自己"不偷看数据"的采集工具，不开源没人信。Readme 写的"本地、可审计"——开源才是真可审计。用户可以自己看每一行采集代码。隐私不是写在政策文本里，是写在代码里让所有人检查。

2. **方法论文件需要网络效应。** 一个人只能产出 N 个方法论。但 100 个人用，方法论库会覆盖你没想过的领域——设计师怎么用 AI、研究者怎么管理文献、写作者怎么打破瓶颈。"由你命名、由你塑造"的方法论，别人的你可以借鉴但不能照搬——但这正是分享的价值。方法论不是通用知识，是带个人印记的模式。读别人的方法论不是"学知识"，是"看别人怎么工作"——这本身就是一种启发。

3. **蒸馏框架需要社区维护。** skills/ 里的内容需要不断更新（依赖升级、API 变更、新工具出现）。开源社区天然适合这种持续维护。一个 skill 文件被 50 个人 fork 和改进，比一个人维护准确得多。

4. **品类定义权。** 率先开源+社区化，KeyPulse 就能定义"方法论文件"的格式标准（类似 Docker 定义了 Dockerfile）。后来的竞品无论闭源还是开源，都得兼容这个格式。

**这一阶段的增长模型：**

不是"下载量"驱动，是**"方法论文件发表量"**驱动。每一次有人在社区分享一个被命名的方法论，就是一次品类教育：

> "哦，原来我每次写代码前那个习惯，可以叫「先搜再挖」。而且有人把它写成了一个 skill 文件，我复制到我的 `.claude/skills/` 里就能用。"

增长飞轮：
```
更多人用 KeyPulse → 更多方法论被发现和命名 → 更多方法论分享到社区 →
品类认知扩大 → 更多人知道"方法论"这个概念 → 更多人用 KeyPulse → ...
```

**关键里程碑：**

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| M5 | 开源发布 | GitHub 公开，README 里的"快速开始"可复现，CI 全绿 |
| M6 | 方法论文件格式标准化 | SKILL.md 格式 + 蒸馏框架规范有独立 repo 和版本号 |
| M7 | 社区方法论库 ≥50 | 覆盖 5+ 不同职业/领域，不止工程师 |
| M8 | CC/Codex 社区认可 | "推荐在 CLAUDE.md 中引用 skills/" 出现在官方文档或社区最佳实践 |
| M9 | 方法论分享机制上线 | 一键导出脱敏方法论文件，一键发布到社区库 |

#### Phase 3：商业化（N→可持续）

**目标**：KeyPulse 本身永远免费。赚钱的是另一个东西。

核心洞察：方法论文件天然是**可商品化的知识资产**。它不是通用课程（任何人都能做），而是经过个人数据验证的、带使用效果背书的工作方式。这给了它独特的定价权。

**产品矩阵：**

```
KeyPulse Capture + Pipeline     ← 永远免费开源 (Apache 2.0)
    │
    ├─ 个人用：方法论发现引擎
    │
    └─ 产出：方法论文件 (method.md / skill.md)
            │
            ├─ 个人库：你自己用，本地存储，永远免费
            │
            └─ 方法论市场 (M³) ← 商业化层
                ├─ 免费层：浏览、下载社区方法论
                ├─ Pro ($8/mo)：方法论分析报告、跨项目对比、AI 辅助命名
                └─ Creator：方法论作者分成（卖方法论包）
```

**三个收入来源：**

**① KeyPulse Pro（$8/mo）— 个人高级分析**

不是解锁功能（那会跟开源精神冲突），而是解锁**分析深度**。

免费版能做的事：
- 采集日常活动，生成日报
- 发现个人模式，命名方法论
- 产出方法论文件（本地存储）
- 浏览和下载社区方法论

Pro 版增加的：
- **跨项目方法论对比**（"你在 KeyPulse 项目里的模式 vs 在工作项目里的模式"）
- **AI 辅助命名和假设提出**（用更好的模型做模式→方法论翻译，Note：这用的是云 API，所以有成本，收费合理）
- **方法论效果追踪**（"自从用了「先搜再挖」，你的 debug 时间降了 40%"）
- **年度方法论回顾**（像 Spotify Wrapped 但是关于你怎么工作的——"今年你命名了 7 个方法论，最高频的是..."）
- **多设备数据合并分析**（如果有多台 Mac）

定位：Pro 是"让方法论操作系统更聪明"的层，不是"解锁基础功能"的层。免费版能产出方法论，Pro 版能理解和优化方法论。这个区分很重要——它不是 freemium 的"功能阉割"，而是"分析深度"的阶梯。

**② 方法论市场（Creator 分成）— 知识交易**

这是最独特的商业化路径，也是 KeyPulse 区别于所有现有工具的地方。

**卖什么：**
方法论作者把命名好的方法论文档 + 配套 skill 文件打包上架。一个方法论包包含：

```
method-thinking-before-asking/
├── README.md                  ← 方法论描述（这个模式是什么、怎么发现的）
├── evidence.md                ← 观察证据摘要（脱敏后的统计数据）
├── skill.md                   ← 配套 .claude/skills/ 文件（CC/Codex 可直接用）
├── variants.md                ← 作者的变体和改进记录
└── usage-data.md              ← 使用效果数据（"用了这个方法论的 N 个人，平均 X 指标改善 Y%"）
```

**为什么有人会买：**
- 这不是通用课程。"如何在 Claude Code 里做 TDD" 是通用知识，YouTube 上有。但 "Harland 在 KeyPulse 项目中经过 6 个月验证的 TDD 工作流" 是独特的——它带有个人品牌和验证数据。
- 买方法论的人买的不是"知识"，是"已经验证过的、可以立刻复制到我的 .claude/skills/ 里的工作方式"。
- 类似 Substack 的付费 newsletter，但是卖的不是观点，是可执行的工作流。

**定价模式：**
- 买断制，一个方法论包 $5-15
- 平台抽 30%（覆盖审核、分发、支付）
- 方法论版本更新（作者持续维护）免费推送给已购用户

**③ KeyPulse Teams（$20/seat/mo）— 团队方法论共享**

这是在隐私约束下的 B 端产品。注意：不是监控员工。

**价值主张：**
> "用 KeyPulse 的团队，新人 ramp-up 快 3 周。因为他们不需要从零学习团队的工作方式——团队的方法论库已经在那里了，而且是从真实工作数据中长出来的，不是某个架构师拍脑袋写的 wiki。"

**工作方式：**
- 个人数据永远在本地。KeyPulse 不碰。
- 只有被命名、脱敏后的方法论文件可以分享到团队库
- 团队库 → 新人 onboarding 直接有一套"这里的人怎么工作"
- 每个方法论文件标注"最早观察于""最近验证于"——不是过时的文档

**为什么团队会买：**
- Onboarding 是真实成本。方法论库直接压缩这个成本。
- 团队 wiki 最大的问题是"写了没人看、看了是过时的"。方法论文件从真实数据里长出来，更新有实际驱动（模式变了就会被发现），不是靠纪律维护。
- "自愿共享"机制避免了"被监控"的抵触情绪。员工决定分享什么。

**为什么这个方法行得通：**

1. **隐私约束不是弱点，是壁垒。** Rewind AI 融资 $35M 做云端记忆，但用户隐私焦虑是它最大的天花板。KeyPulse 的本地优先架构一旦被信任，竞品没法复制——因为 SaaS 基因的公司做不到真正的本地。架构决定信任，信任决定留存。这是结构性优势，不是 feature 优势。

2. **方法论品类是蓝海。** "效率工具"是个红海（Todoist、Things、Notion、Linear...），"AI 助理"正在变成红海（Copilot、CC、Codex、Cursor、Windsurf、Devin...）。但"方法论操作系统"——一个帮你发现、命名、优化、分享**你是怎么工作的**的系统——没有人在做。最接近的是 Obsidian（知识管理）和 Roam Research（思维工具），但它们的关注点是"你知道了什么"，不是"你是怎么做的"。KeyPulse 盯着的是过程而不是结果，是方法而不是知识。

3. **AI 工具的普及是 KeyPulse 的顺风。** CC、Codex、Copilot、Cursor 用的人越多，"怎么跟 AI 协作"就越是一个需要被命名的方法论。就像 1990 年代"怎么用搜索引擎"是一个需要被命名的技能一样。KeyPulse 是唯一盯着人机协作方法论交叉点的工具。AI 工具的每一波增长都是 KeyPulse 的免费获客。

4. **方法论市场有网络效应。** 每个新方法论都会扩大品类的认知边界。不像社交网络（需要用户量才能用），方法论市场的价值在第一个精心制作的方法论发布时就存在。买第 3 个方法论的人会发现前 2 个也值得买。这不是"先有鸡还是先有蛋"的问题——第一个精心制作的方法论就是第一只鸡。

### 9.3 竞争格局

| 谁 | 做什么 | 为什么不冲突/为什么危险 |
|----|--------|------------------------|
| Obsidian | 知识管理，本地 markdown | 互补。KeyPulse 的产出落在 Obsidian 里。可能是渠道而非竞品 |
| Rewind AI | 云端记忆，全量录屏 | 定位不同（Rewind 是"记忆"，KeyPulse 是"方法论"）。但 Rewind 的隐私争议会让用户流向 KeyPulse |
| Raycast / Alfred | 启动器+工作流 | 工具层，不碰"模式"层。但 Raycast 如果做 AI 协作功能可能撞车 |
| 量化自我类 (RescueTime, Toggl) | 时间追踪、效率评分 | KeyPulse 明确拒绝这个定位。但用户认知可能混淆——需要品类教育 |
| AI 助理类 (CC, Codex, Copilot) | 替你写代码 | 不冲突。KeyPulse 观察你怎么用它们，不是替代它们 |
| 大厂 (Apple Screen Time, Microsoft) | 屏幕时间统计 | 粗粒度、无方法论层。但如果 Apple 做"模式发现"功能，KeyPulse 需要足够深的护城河 |

最大的风险不是某个竞品，而是**大厂把"方法论发现"作为 OS 级功能内置**。如果 macOS 16 自带"Work Patterns"功能，KeyPulse 需要靠社区+方法论市场+跨平台来防御。

### 9.4 关键风险

| 风险 | 影响级别 | 缓解策略 |
|------|----------|----------|
| 开源后大厂复制架构做 SaaS | 高 | 品牌 + 社区是护城河。Obsidian 开源了但没人能复制它的社区。方法论市场是网络效应壁垒 |
| 方法论市场冷启动 | 中 | Phase 2 先攒方法论文件，Phase 3 再做市场。鸡生蛋问题靠 Phase 2 社区贡献解决 |
| 隐私丑闻（一次泄露就死） | 极高 | 架构已做隔离（采集→隐私→存储三层）。开源可审计比闭源更安全。永不做云端采集 |
| macOS 天花板 | 中 | Windows/Linux 支持在 Phase 2 由社区贡献。采集层已抽象（watcher 接口），移植成本可控 |
| 笔友人设在商业化中崩坏 | 极高 | 产品哲学三条红线写进公司治理文件/贡献指南。商业化层只碰方法论文件，不碰原始数据。笔友语气是 A/B test 免疫项 |
| 大厂 OS 内置模式发现 | 中 | 社区 + 跨平台 + 方法论市场深度。如果 Apple 做了，KeyPulse 的差异是"可分享的方法论"和"跨平台" |

### 9.5 路线图时间线（推测）

```
2026 Q2-Q3  Phase 1: 个人工具稳定
  ├─ 蒸馏框架落地 (本文第七节)
  ├─ 跨日模式 MVP
  └─ 第一个命名的方法论产出

2026 Q4-Q1  Phase 2: 开源
  ├─ GitHub 公开，文档完备
  ├─ 方法论文件格式规范独立发布
  └─ 社区方法论库启动

2027 Q2-Q3  社区增长
  ├─ 社区方法论库 ≥50
  ├─ CC/Codex 社区认可
  └─ 方法论分享机制上线

2027 Q4     Phase 3: 商业化启动
  ├─ KeyPulse Pro ($8/mo) 上线
  └─ 方法论市场 Alpha（邀请制 Creator）

2028        规模化
  ├─ 方法论市场开放
  ├─ KeyPulse Teams ($20/seat/mo) 上线
  └─ Windows/Linux 社区版稳定
```

### 9.6 一句话总结

**开源 KeyPulse core + 蒸馏框架规范，让"个人方法论"成为一个被认可的品类。然后在这个品类上建一个方法论市场——卖的不是工具、不是数据，是"如何工作的知识"。**

Readme 第 233 行已经写了终局：**"把这件事变成一个你可以回看、甚至可以分享的东西"**——市场就是"分享"的那个环节。不是后来加上的商业模式，是产品基因里本来就有的终点。
