# Weekly Golden Spec (exec)

> Synthesized from W19/W20/W21 (acceptable baselines) after the W22 regression
> (40KB, score 51). This is the regression anchor: any weekly prompt/render
> change must be diffed against it via `keypulse eval weekly`.
>
> Authored 2026-06-03 during the W22 root-cause + deep-fix pass.

## 1. Target structure (exec)

Sections, in this exact order (validator enforces order + presence):

```
---
tags: [weekly, weekly/exec]
aliases: ["YYYY-Wnn (M/D – M/D)"]
---
# 本周工作汇报 (YYYY-Wnn, M/D-M/D)

## TL;DR
<one line: 主题数 + 完成/推进中分布 + top 2-3 主线名。derived, not LLM filler>

## 关键数据
| 维度 | 数值 |   — 决策 / 推进 / 新启动 / 协作 / 产出

## 本周关键进展
### {icon} {topic name} | {状态}        ← icon+badge REQUIRED (✅完成/🔄推进中/🆕启动/⚠️卡住)
<narrative: 1 段, 事实句在前 + ≥1 判断句, 含 ≥2 个 [[日期]] 锚点>
→ 关键决策: <≤3 条, 分号分隔>
→ 可见产出: <≤3 条>
→ ⚠️  卡点: <≤3 条>
... (3–8 个 section, 一个主题一个)

## 本周风险           | 表格, ≤3 行
## 没接住的球          | 条目含 [[日期]] + 未跟进语义, 或「本周没有掉球」
## 一个观察            | 跨周视角的问句(以 ? 结尾) + 「- 证据: …」
## 跨周差异
## 本周新沉淀原则       | ≤8 条, 语义去重; 无显著原则则整段省略
## 下周锚点            | ≤5 条

---
> 生成信息: 质量 NN/100 · …
```

## 2. Per-section hard caps

| 段 | cap | 理由 |
|---|---|---|
| 本周关键进展 section 数 | 8 | 同主题必须合并, 不按 feature/day 碎段 |
| 每段 关键决策/可见产出/卡点 | 3 条 | 防 run-on 巨型决策行 |
| 本周新沉淀原则 | 8 条 (语义去重后) | 周报是精选不是 dump |
| 本周风险 | 3 行 | |
| 下周锚点 | 5 条 | |

## 3. Anti-patterns (W22 实测命中)

- **A1 段标题缺状态徽章** — `### CHAT-0410 …` ❌ → `### ✅ CHAT-0410 … | 完成` ✅
- **A2 同一事实跨段重复** — "rewrite.py 320→815"、"第三次重开"、"M2/M1同源" 在 5 个 section 重复 ❌。每条事实/决策只能出现在其归属 section。
- **A3 同项目碎成多 section** — Carmind_code 拆成「LLM预检」「m4a」「前端交互」3 段 ❌。一个项目一个 section。
- **A4 原则 firehose** — 190 条含大量近义重复（data-input-quality-context / data-quality-context / data-input-quality-importance …）❌。语义去重 + cap 8。
- **A5 → 关键决策 巨型 run-on** — 一条 `→ 关键决策` 塞 5 个决策、200+ 字 ❌。
- **A6 决策错配** — failure-stack 段出现 Carmind 决策（全局 fallback 误配）❌。
- **A7 TL;DR 纯模板** — "本周主线集中在{topic名拼接}" 是套话; TL;DR 要带状态分布/计数，承载信息。
- 黑名单短语见 `weekly_validator.ANTIPATTERNS`（复盘/赋能/卓有成效/虽然…但…推进了 等）。

## 4. Quality gate

- `keypulse eval weekly --candidate <path> --style exec` 五维：结构完整性 / 内容覆盖度 / 文本质感 / 客观性溯源 / 反模式检查。
- 退化门槛：**总分 ≥ 90，且结构完整性 = 100**（TL;DR + 段序齐全）。
- 人眼复核（metric-driven-delivery）：文件 ≤ ~12KB；无 A1–A7。

## 5. Baseline anchors

- `2026-W19-exec-v3.md` / `2026-W21.md`（vault, score 94）— 结构/文风参照
- `2026-W22-postfix.md`（2026-06-04 深修后真链路产出）— 结构回归锚点。
  **40KB→10KB、51→71、结构 80→100、原则 190→8、Carmind 4 段→1、KeyPulse 3 段→1**。
  深修核心：weekly section 改按 **canonical 实体**分组（`_collapse_topics_by_canonical_entity`），
  不再按 daily-cluster-slug 碎段；`_assign_signals_to_topics` 降级为实体合并不可用时的兜底。
  **残留未达 90 的内容层缺口**（非结构 bug）：`没接住的球: 本周没有掉球` 触发 validator 误罚（不准造假锚点 gloss）、
  Carmind/CHAT-0410 两个独立实体的轻度语义重叠、daily 源噪声漏出（英文 coverage、卡点行无标点）。
