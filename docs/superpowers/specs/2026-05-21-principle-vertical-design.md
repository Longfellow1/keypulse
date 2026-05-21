# Principle Vertical — Design Spec

**Date:** 2026-05-21
**Status:** Approved by user, ready for implementation plan
**Author:** Brainstorming session (Opus 4.7 + user)

## 背景

KeyPulse 当前 anchor 系统抓的是"项目长尾"（事件 / 主题 / 做了什么），漏抓用户的**方法论层**——判断标准、设计原则、反模式、类比、元决策。这层信息跨项目、长期有效、可复用——项目结束了它们还在。

**触发素材**：用户 2026-05-21 跟 Codex 讨论 React workspace 设计时产生的对话片段。现场从一段对话中抽出 6 个方法论碎片，证明数据原料存在且可识别。

**产品定位升级**：KeyPulse 当前是"项目记忆"，加 principle vertical 后是"**元认知镜子**"——你跨项目的判断逻辑、设计直觉、反模式库自动沉淀。这层是 second brain 工具（Roam/Obsidian/Logseq）一直手动收集的东西，KeyPulse 能自动抓。

## V1 MVP 范围

### 消费场景（用户拍板）
1. **被动反馈镜像** — 不主动找，存在 Obsidian graph 上自然消费，提醒"原来我这周/这个月的思维结构是这样的"
2. **周复盘 / 写周报时** — "这周我又积累了什么判断方式"，weekly note 加一节

**明确排除**（V2 阶段做）：
- 现场决策检索 / 查询界面
- 导出 / 分享接口（写文章、带新人、做 slides 素材）

### 抓取策略（用户拍板）

**宽抓模式 + 一周人眼审决定保留什么。**

- LLM 谁算 non-event 陈述都 dump（原则 / 反模式 / trade-off / 类比 / 直觉 / 元决策 / 吐槽）
- 不预设权威 schema，不预设触发词
- 让 LLM 自标 `kind` 字段，一周后看分布决定要不要收敛枚举
- 跟 [[feedback_metric_driven_delivery]] 一致——人眼读完才算交付

## 架构

```
键盘 chunk（V1 唯一数据源）
    ↓
新 capability: L7_principle_distillation（每日 pipeline 增量）
    ↓
候选 principle 列表（per chunk batch）
    ↓
出口 1: vault/principles/<date>-<slug>.md（每条独立文件）
出口 2: weekly note "## 本周新沉淀原则" 节（聚合呈现）
```

### 新增组件

- `keypulse/prompts/L7_principle_distillation.v1.md` — capability prompt
- `keypulse/prompts/schemas/L7_principle_distillation_input.json` — input schema
- `keypulse/prompts/schemas/L7_principle_distillation_output.json` — output schema
- `keypulse/pipeline/principle_distillation.py` — pipeline 节点
- `keypulse/obsidian/principle_exporter.py` — vault 输出
- Weekly orchestrator 加一节渲染

### 不改的部分

- 不改 watcher（键盘 chunk 已有）
- 不改 anchor / daily / weekly 核心算法
- 不影响 daily/weekly 现有出口

## 数据 Schema

每条 principle 一个 .md 文件：

```yaml
---
principle_id: <stable-english-slug>
distilled: "一句话抽象，可复用规则形式"
kind: principle | anti-pattern | trade-off | analogy | hunch | meta  # LLM 自标，不强约束
source_date: 2026-05-21
source_context: "来自 daily 2026-05-21 cluster X 或 anchor Y，或独立"
confidence: 0.85  # LLM 自评 0-1，人眼审用
tags:
  - principle
  - principle/<kind>
---

## 原话引用

> 30-200 字原始片段，保留语境

## 抽象提炼

distilled 的扩展版（1-3 句），如果有上下文需要补充

## 上下文

来自 [[<source-daily-or-anchor>]]
```

文件名：`YYYY-MM-DD-<distilled-slug>.md`，**不挂 project tag**（挂"你"）。

## Pipeline 节点

### L7_principle_distillation 输入

```json
{
  "date": "2026-05-21",
  "chunks": [
    {
      "chunk_id": "...",
      "ts_start": "...",
      "text": "原始 chunk 内容",
      "context": "可选：哪个 cluster/anchor"
    }
  ],
  "known_principles": ["principle_id1", "principle_id2", ...]
}
```

`known_principles` 给 LLM 看"已沉淀的原则列表"，避免重复抽取（但 V1 不做合并，看到重复也接受，一周后看模式）。

### L7_principle_distillation 输出

```json
{
  "principles": [
    {
      "slug": "ux-mental-model-alignment",
      "distilled": "同类操作必须心智对齐，不让用户学多套策略",
      "kind": "principle",
      "confidence": 0.92,
      "quote": "操作区的心智还是没跟批量任务对齐，我感觉。批量任务是左侧是1操作区，右侧一大块是2+3...否则用户学2套操作策略，认知复杂度高、迁移成本高",
      "source_context": "对话 2026-05-21 跟 Codex 讨论 React workspace"
    }
  ]
}
```

## 调用时机

每日 pipeline 在 daily orchestrator 完成 anchor gateway 后追加 L7 节点。L7 完成后写入 vault/principles/。

不阻塞 daily 主流程（异常 fall through 记日志）。

## Weekly 集成

Weekly orchestrator 渲染时，扫本周 `vault/principles/<W-week-date-range>*.md`，在 weekly note 内增加一节：

```markdown
## 本周新沉淀原则

- [[2026-05-21-ux-mental-model-alignment]] — 同类操作必须心智对齐
- [[2026-05-21-info-density-over-decoration]] — 信息密度低的元素该删
- ...
```

**plain style** 和 **exec style** 都加这节，渲染方式相同（无 style 差异）。

## V1 MVP 验证标准（一周后人眼审）

跑一周（7 天 daily pipeline），然后用户人眼审 vault/principles/ 累计产物：

- ✅ **≥ 3 条让用户说"这个我以前没意识到自己说过"**
- ✅ **垃圾率 < 20%**（套话 / 无信息）
- ✅ **人眼整理时间 < 10 分钟/周**（一周一次审，超过 10 分钟说明信噪比太差）

任一不达标 → 改 prompt 再 run，不能直接进 V2。

不在 V1 验证范围内的指标（不做硬限）：
- 每日产生条数（不规定上下限）
- distilled 字数（让 LLM 自由）
- kind 标签分布（先看 LLM 自标，不预设期望）

## 未来扩展（明确 roadmap）

### V2 — V1 验证通过后做
- **现场检索 / 查询界面** — `keypulse principle search "心智对齐"` CLI 命令
- **导出 / 分享接口** — `keypulse principle export --topic ux --format markdown` 输出可直接发布的素材

V2 数据 schema 已经留好 hooks：`kind`/`confidence`/`source`/`quote` 都是 V2 查询和导出会用到的字段。V1 直接落对。

### V3 — V2 跑稳后看一周原料模式再定
- 同义合并工具（参考 anchor 的 propose-anchor-merges + merge-anchors）
- 类型枚举收敛（看 LLM 自标的 `kind` 实际分布，决定要不要锁定 6 个或扩展到 10 个）
- 触发词预过滤（看抓全 vs 漏抽象的 trade-off 真表现，再决定是否引入关键词预筛省 token）

### 永不做
- 区分"对 AI 对话" vs "对自己思考"——原则就是原则，来源无关
- 跨用户共享 / 团队特性——KeyPulse 是个人工具

## 成本预估

每日 LLM 调用 +1 次（L7）：
- 输入 ≈ 当天键盘 chunk 摘要（10-30k tokens）
- 输出 ≈ 200-1000 tokens
- standard tier 日均 +$0.05~0.15

可接受。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 抽到太多噪音，人眼审时间爆炸 | V1 验证标准：人眼整理 < 10 分钟/周，不达标改 prompt |
| 抓到重复同义原则 | V1 接受重复，攒一周后看模式再决定合并策略（V3） |
| `kind` 标签 LLM 自由打导致 graph 太散 | V1 接受，看分布再决定枚举（V3） |
| 键盘 chunk 抓不到关键对话（如网页里的输入） | V1 接受这个限制；V1 验证后若发现重要漏抓，V2 评估加 AX text 输入 |
| `<object object>` bug 之类的事故 | 跟 anchor 系统一样测试覆盖 + conftest sentinel |

## 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 每条独立文件，不做每日汇总单文件 | 消费场景是被动镜像，graph 节点多反而好（视觉分离） |
| 2 | 不挂 project tag | 这层属于"你"不属于项目 |
| 3 | `kind` 不枚举 | 早期让 LLM 自由打标，避免约束认知；一周看分布再决定 |
| 4 | 文件名带日期前缀 | graph 上时间维度可视化；避免不同时期同主题原则文件名碰撞 |
| 5 | weekly 双 style 同渲染 | 这节"原则"不分自用/汇报，都一样呈现 |
| 6 | V1 不做检索/导出/合并/收敛 | 先调优质量，效果达标再扩出口（V2/V3） |
| 7 | 跟 anchor pipeline 隔离 | 不复用 anchor 概念，纯新 vertical |

## 实现优先级

按 milestone 拆分，writing-plans skill 来规划具体顺序。本 spec 不规定实现顺序，但实现 plan 应涵盖：

1. L7 capability prompt + schemas
2. principle_distillation pipeline 节点
3. principle_exporter（vault 输出）
4. daily orchestrator 集成
5. weekly orchestrator 集成
6. 测试覆盖（unit + integration + golden set）
7. 跑一周真实数据采集验证
