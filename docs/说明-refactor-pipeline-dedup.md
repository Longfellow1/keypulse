# Refactor: 管道减法 — 聚类/段名/数据模型去并存

- **日期**: 2026-06-03
- **状态**: 计划（仅计划，未动手）
- **前作**: [`refactor-daily-renderer-unification.md`](./refactor-daily-renderer-unification.md)（2026-05-10，6→1 renderer 统一）
- **目标**: 把 daily/weekly 管道里"同一概念多套并存实现/字段"收敛到单一权威路径，并**立契约挡退化**，杜绝第三次长回来。

---

## 根因（一句话）

KeyPulse 上次（2026-05-10）已把 6 个 renderer 统一成单一入口 `daily_summary.render_daily_markdown`，但**统一后没立契约**——之后 anchor 双步法、flagship 一步法两条新聚类路径叠加上来，绕过单一入口，段名重新变回四源、聚类变回两条、数据模型变回 clusters/topics 双份。

> 体积不是病，**"减完又长回来"才是**。复发的根因：没有单一权威路径 + 没有挡退化的契约。
> 归类：System boundary 失败 + 补丁扩散 → 升维删旧 + **这次补上契约测试**。

症状（都是同一根因的表现）：段名「完成 XX」回归（6/3 日报 6 段 5 脏）、trace 数字不自洽（6/2 things=3 但 H3 0→0→0）、修 bug 打偏路径（80a4285 修了 weekly 渲染却没碰 daily 主路径）。

---

## 审计事实（已核实）

### 两条聚类路径同次都跑（真并存）

| 路径 | 入口 | 产出 | 调用点 | 状态 |
|---|---|---|---|---|
| flagship 一步法 | `FlagshipSingleStepStrategy` (daily_strategy.py) | `clusters[]`（含 narrative_markdown 的 `###`） | `daily_orchestrator.py:1524` | ✅ 在跑 |
| anchor 双步法 | `anchor_today_clusters` (weekly_topic_anchor.py:361) | `topics[]` + anchor assignments | `daily_orchestrator.py:1309` | ✅ 在跑 |

→ 这是段名四源、clusters/topics 双模型的**总根**。

### 段名四源

| 位置 | 字段 | 来源 |
|---|---|---|
| `daily_orchestrator.py:387` | `display_name` | flagship LLM 直产 `###`（旁路） |
| `daily_orchestrator.py:1381` | `display` | L0_anchor 的 `anchor_display` |
| `daily_summary.py:1288` | `heading` | render 层读 `topic.display`（**最终 vault 段名取这套，最脏**） |
| `weekly_topic_anchor.py:569` | `anchor_display` | cluster.display_name 兜底到 anchor.display |

实测 6/3：`clusters[].display_name` 与 narrative 的 `###` 均干净（`AnyInt API密钥与流量券流程确认`），唯独 `topics[].display` 脏（`AnyInt-完成流量券与资源包更新`），而 vault 段名逐字取了它。

### 数据模型并存

- `clusters[]`（flagship 产）vs `topics[]`（anchor 产）：`display_name≈display`、`narrative_one_line≈narrative`，同一批聚类两套表示同存 JSON。
- `narrative_markdown`：既存完整 markdown 文本，又被 validator/judge **逐行二次解析**抽 `###` 做一致性检查——结构化数据与非结构化文本重复表达，漂移风险高。

### 零消费候选（已 grep + codegraph 双核实）

| 符号 | 调用点 | 结论 |
|---|---|---|
| `filter_daily_event_cards` (daily_summary.py:1088) | 0 | 真零消费，`:1310` 有 M4 待删标记 |
| `_daily_event_cards` (daily_summary.py:1029) | 0 | 真零消费 |
| `_cross_day_continuations` (daily_summary.py:1071) | 0 | 真零消费 |

→ events 卡片旧能力已被 narrative 内嵌 entity 取代（0526/0527 deep-fix），是有意下线，非接线断。

---

## 减法分三层（按风险/收益排序）

### 第 1 层 · 段名单一出口〔先做，低风险〕

- **动作**：① `render_daily_markdown` 段名只认 `cluster.display_name`（确定性、干净），`topic.display` 降为兜底；② 废掉 `daily_orchestrator.py:387` LLM 直产 `###` 旁路，所有写盘走 render 单一入口。
- **立契约**：段名契约测试——daily/weekly 段名只能由单一函数产出，任何旁路产 `###` 即测试红。**这是与上次重构的唯一区别，防第三次长回来。**
- **不碰 LLM**：干净来源是结构化数据，不违反"不用死规则砍 LLM"。
- **验证**：6/3 重渲染，段名 == `clusters[].display_name`，「完成」前缀消失。
- 风险：低 ｜ 收益：治"找根因难" + 治段名「完成」回归。

### 第 2 层 · 删确认死代码〔零债，低风险〕

- 删 `filter_daily_event_cards` / `_daily_event_cards` / `_cross_day_continuations`。
- **护栏**（无回归）：删前确认 events 卡片功能确已被 narrative 内嵌 entity 取代。

### 第 3 层 · 聚类路径解耦〔根治，高风险，缓做〕

- flagship + anchor **不二选一**（影响分析证明两条各有独占消费方，见下文）。改为"一次聚类 + anchor 注解层"，消除 clusters/topics 双模型。
- **时机**：daily/weekly 内容质量仍在调，本层等内容定版后单独做，避免与质量调优互相污染。
- 风险：高（weekly L4–L6 全依赖 topics[]），必须全消费方迁移后才删旧结构。

---

## 防退化（写进规矩）

- 每层减完配契约测试（段名单源 / 数据模型单源）。
- CLAUDE.md「输出质量标准」旁新增一条：**新增聚类/渲染路径必须删旧，不许并存。**

---

## 第 3 层决策：flagship vs anchor 消费方影响分析

**关键发现：两条路径不能二选一——它们不是冗余，是职责纠缠。**

| 路径 | 独占消费方 | 删了断什么 |
|---|---|---|
| flagship（产 clusters[] + narrative_markdown） | `narrative_markdown` 是 daily markdown 文本主源；clusters[] 16 个消费方约半数可被 topics 替代，`merge_candidate_with` 无替代 | daily 文本**降级**到 `topic.narrative`（`daily_summary.py:1296-1298` 有回退，非"完全无文本"，但质量降） |
| anchor（产 topics[] + anchors.json） | 17 个消费方**全独占**：daily 锚定/topics 构建/validator 8 个、weekly L4–L6 4 个、CLI/导出/judge 5 个 | weekly 跨日锚定**彻底断**（L4/L5/L6 全依赖 topics[]）、anchor note、anchors.json 级联失效——难补 |

→ flagship 独占"整体叙事文本"，anchor 独占"结构化跨日锚定"。各有不可替代的产出，单边删任一条都会断关键消费方。

### 修正方向：不删路径，解耦职责（消除双模型，保留双价值）

把"两条并行聚类"改为"**一次聚类 + anchor 作为注解层**"：

1. 聚类只做一次，产**单一**聚类真值（统一为 clusters[]）。
2. anchor 不再独立重新聚类产 topics[]，改为**消费 clusters[] 做锚定标注**（把 anchor / anchor_state / derived_from 作为 clusters 上的注解字段，或 topics 退化为"clusters + 锚定视图"）。
3. narrative 文本基于这份单一聚类产。
4. weekly 改读统一结构（迁移 L4–L6 的 topics[] 依赖）。

效果：clusters/topics 双模型消失、段名回到单源，同时**两边独占价值都保住**。这才是根治，不是删功能。

---

## 执行顺序

1. 第 1 层（段名单一出口 + 契约）← 随时可动手
2. 第 2 层（删死代码）← 随时可动手
3. **先治 weekly 内容质量**（M5，本计划之外的并行任务）
4. weekly 内容定版后 → 第 3 层（聚类路径解耦）
