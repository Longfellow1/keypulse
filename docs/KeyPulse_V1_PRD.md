> **已被 `docs/KeyPulse_V2.md`（KeyPulse V1 PRD｜开发基线 v1.2）取代，本文仅作历史参考。**

# KeyPulse V1 PRD

> **版本定位**：产品基线 · V1  
> **说明**：本文记录已对齐的产品目标、核心场景与能力边界，属于产品需求基线；P0/P1/P2 为规划优先级，不代表所有功能均已实现。

## 1. 产品定义

**KeyPulse 是一个持续采集、整理并安全保存个人数字上下文的本地 Memory & Context 基础设施，并把这些记忆和上下文开放给任何 AI 调用。**

KeyPulse 不试图成为新的通用 AI Assistant，也不要求用户维护传统知识库。

核心价值：

> **把散落、易失、跨软件的个人数字经历保存下来，让任何 AI 能够记住、找回并复用用户过去的事实、经验和方法。**

---

## 2. 核心场景

顶层只分两类。

### 2.1 工作

#### 找回来

- 昨天和某人讨论了什么
- 某个技术方案叫什么
- 某功能之前如何定义
- 为什么当时做这个决策
- 上次类似问题最终怎么解决
- 某个任务做到哪里了

#### 沉淀与复用

长期积累：

- Decision
- Outcome
- Task
- Preference
- 失败案例
- 产品原则
- 判断逻辑
- 工作经验
- 沟通方式
- Workflow / Method / Skill

最终由 AI 从真实历史中形成可复用能力。

### 2.2 生活

#### 找回来

- 和谁聊过什么
- 上次买的商品是什么
- 某个网页、链接在哪里
- 上次旅行查过什么资料
- 某件生活事务之前怎么办的

#### 沉淀与复用

长期形成：

- 消费偏好
- 旅行经验
- 办事流程
- 踩坑记录
- 人际信息
- 个人习惯
- 可复用生活经验

---

## 3. 三层产品架构

### L1 · Personal Data Layer

**把真实发生过的东西留下来。**

这是 KeyPulse 最底层、也是最重要的差异化能力。

#### A. 动态数据

持续捕获容易消失的信息：

- Keyboard
- Window / AX
- Clipboard
- Browser
- App Activity
- 其他瞬时数字行为

#### B. 已存在的结构化数据

统一接入：

- Claude / Claude Code
- Codex
- Cursor
- Git
- Terminal
- Browser History
- Files / Markdown
- 飞书
- 微信
- 后续其他 AI / App

#### C. 数据处理

统一执行：

```text
Raw Data
   ↓
清洗 / 去噪 / 去重
   ↓
脱敏 / 权限处理
   ↓
时间 / 人 / 项目 / 来源识别
   ↓
Event / Conversation / Artifact
   ↓
Local Personal Data Store
```

原则：

> **Capture First。先保证事实不丢失，再谈智能。**

---

## 4. L2 · Memory & Context Layer

**把“数据”变成真正可使用的个人记忆。**

这一层需要完整覆盖 Brain、Memmy 类产品已经验证的核心能力。

KeyPulse 不能只保存 Raw Data，需要同时维护：

### 4.1 Episodic Memory

记录：

> **发生过什么。**

例如：

- 一次对话
- 一次会议
- 一段开发 Session
- 浏览过某页面
- 修改某文件
- 执行某命令

### 4.2 Semantic Memory

记录：

> **从历史事实中已经确认了什么。**

例如：

- 项目背景
- 产品定义
- 技术架构
- 人物关系
- 长期事实
- 用户偏好

### 4.3 Operational Memory

记录：

> **工作当前处于什么状态。**

核心对象包括：

- Project
- Task
- Decision
- Outcome
- Preference
- Open Thread
- Artifact

例如：

```text
Decision:
采用方案 B

Reason:
方案 A 在测试中存在 XX 问题

Outcome:
后续验证通过

Evidence:
Claude Session + Git Commit + Test Result
```

### 4.4 Experience Memory

记录：

> **过去是怎么把事情做成或做失败的。**

包括：

- Successful Pattern
- Failed Attempt
- Lesson
- Workflow
- Heuristic
- Principle
- Method

这一层是未来 Skill 蒸馏的重要数据基础。

---

## 5. Memory 生命周期

Memory 不是“写进去就不管”。

需要支持：

```text
Capture
↓
Extract
↓
Merge
↓
Retrieve
↓
Update
↓
Outcome Verification
↓
Decay / Archive
```

解决：

- 重复 Memory
- 已过期 Memory
- 冲突事实
- 未完成 Decision
- Decision → Outcome 闭环
- Preference 更新
- 项目状态变化

核心原则：

> **Memory 必须反映当前事实，而不仅仅保存历史文本。**

---

## 6. Context Service

**让任何 AI 能使用这些 Memory。**

提供统一能力：

- Search
- Recall
- Get Context
- Timeline
- Project Context
- Person Context
- Task Context
- Decision / Outcome
- Preference
- Evidence
- Recent Activity
- Related Memory

支持按照以下维度组合检索：

- 时间
- 人物
- 项目
- App
- Topic
- Source
- Memory Type

### 6.1 Cross-Agent Continuity

用户从：

```text
Claude
↓
Codex
↓
Cursor
↓
WorkBuddy
```

切换时，不应该重新解释自己。

不同 Agent 使用的是：

> **同一份 KeyPulse Memory。**

因此必须支持：

- 跨 Agent Session 历史
- Shared Personal Memory
- Shared Project Context
- Agent 写入 Memory
- Agent 查询 Memory
- Source / Evidence 回溯

### 6.2 接口

优先：

1. **MCP**
2. CLI
3. Local API
4. 后续 Host Adapter / Skill

原则：

> **One Memory Core，Multiple AI Interfaces。**

---

## 7. L3 · AI Application Layer

**KeyPulse 提供记忆，AI 提供智能。**

主要消费者：

- Claude
- Codex
- Cursor
- WorkBuddy
- Qwen
- 其他 AI IDE / Agent

典型能力：

#### Recall

> “昨天和小李聊的那个技术框架叫什么？”

#### Resume

> “接着昨天 KeyPulse 的工作继续。”

#### Analyze

> “为什么之前选择了这个方案？”

#### Learn

> “总结我过去处理这种问题的经验。”

#### Distill

> “把过去半年 Agent 评测经验整理成 Method。”

#### Skill

> “把这套稳定方法转成一个 Skill。”

因此：

```text
Personal Data
     ↓
Memory
     ↓
Context
     ↓
AI Reasoning
     ↓
Method / Skill / Action
```

KeyPulse 不需要自己重新造一个大型 Agent。

---

## 8. KeyPulse Client / HUD

HUD 保留，但职责不再是日报 Dashboard。

它是：

> **Presence + Status + Trust Surface**

用户需要知道 KeyPulse：

- 是否正在运行
- 是否正在采集
- 最近一次采集时间
- 数据库是否健康
- 当前有哪些 Sources
- 哪些 AI 正在连接
- 最近有哪些 Memory 更新
- 是否出现权限/数据异常

轻量呈现（概念示例，并非实时数据）：

```text
KeyPulse ● Healthy

Today
2,431 events
7 memory updates
3 active topics

Sources
Claude       ●
Browser      ●
Git          ●
WeChat       ●

AI
Claude       Connected
Cursor       Connected
Codex        Connected

[ Search ] [ Pause ] [ Privacy ]
```

HUD 提供陪伴感，但不承担主要内容消费。

---

## 9. Always-on 原则

KeyPulse 保留 Always-on。

原因：

> **部分动态数字上下文如果没有实时留下，将永久消失。**

但：

> **Always-on ≠ Always-inference**

后台常驻主要执行：

- Capture
- Event 写入
- 基础清洗
- 去重
- Health
- Source Sync

复杂模型能力：

- 聚类
- Memory 提炼
- Method
- Principle
- Skill

可以延迟、批量或按需运行。

---

## 10. 隐私原则

### Local First

默认：

- 数据本地保存
- 数据库本地
- 索引本地
- 敏感字段本地处理

### Context Boundary

工作、生活及不同敏感等级的数据需要支持独立 Bucket。例如：

```text
Work
Personal
Private
Financial
Temporary
```

AI 获取数据前，根据：

- Source
- Bucket
- Agent
- Query

决定允许暴露的 Context。

---

## 11. V1 优先级

### P0 — 数据与 Memory 基础

- 动态 Capture 稳定化
- Claude / Codex / Git / Terminal / Browser / Files
- Local DB
- 清洗 / 去重 / 脱敏
- Event
- Episodic Memory
- Semantic Memory
- Project / Task / Decision / Outcome / Preference
- Search / Recall
- Evidence
- MCP
- HUD Health / Source Status
- Pause / Privacy

### P1 — Context Intelligence

- 人物识别
- 项目归属
- Topic
- 跨 Source Context
- Open Thread
- Memory Merge / Update
- Memory 生命周期
- Context Assembly
- 飞书 / 微信
- Cross-Agent Continuity

### P2 — Experience & Skill

- Failed Attempt
- Lesson
- Method
- Heuristic
- Principle
- Workflow
- Skill 生成
- 长期 Pattern Discovery

---

## 12. 明确不做

V1 不重点做：

- 重型 Dashboard
- Obsidian 作为主要前端
- 日报 / 周报作为核心产品
- 自建通用 AI Assistant
- 自建大型 Agent Runtime
- 实时 LLM 分析全部数据
- 强迫用户人工维护知识库
- 自动猜测所有行为意图

---

## 13. KeyPulse 的产品边界

以后所有需求都问两个问题。

### 第一问

> **它能否让个人真实数字经历保存得更完整？**

对应 L1。

### 第二问

> **它能否让这些经历更可靠地成为 AI 可使用的长期 Memory / Context？**

对应 L2。

两者都不是：

> 原则上不进入 KeyPulse Core。

---

## 14. 产品一句话

> **KeyPulse 是 AI 的本地个人记忆与上下文数据库。**

它既拥有 Brain / Memmy 类产品的：

> **长期 Memory + 跨 Agent Context**

又比它们多一层：

> **持续捕捉用户真实数字生活的 Data Layer。**

最终形成：

**真实数字经历 → 长期 Memory → 任意 AI。**
