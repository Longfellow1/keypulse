# 知识图谱设计 — 日报周报连成主题档案

> 决策日期：2026-05-20
> 状态：待实施
> 核心理念：日报是流水账，主题档案是故事。

## 目标

让用户能用**主题视角**回顾过去，而不是只能按日期翻日报。

**用户价值**：
- 一个文件看完一件事的来龙去脉（从立项到上线，中间踩了哪些坑、卡了几天、被什么打断过）
- 3 个月前的事按主题名直接调出来，不用记日期
- Obsidian 关系图自动告诉用户"V3 上线"其实和"V2 稳定化"一脉相承

**用户体验**：4 个入口都是 Obsidian 自带功能，零学习成本
1. Obsidian 关系图（最直观）：主题间派生/引用关系直接画出来
2. 文件夹浏览：左侧 `anchors/` 列所有主题，可按"最近活跃"排序
3. 从日报跳进来：日报里的 `[[v3-rollout]]` 链接点进档案
4. 反向链接面板：站在某主题档案上看"哪些日报提过我"

---

## 核心设计

### 节点 = anchor 主题档案
- 物理形态：`<obsidian-vault>/anchors/<slug>.md`
- 每个主题一个文件，跨周延续的同主题对应同一文件
- 文件内容由 KeyPulse 自动渲染，是 read-only sink（用户编辑会被覆盖）

### 边 = 两种关系
- **派生**（继承/分支）：B 主题从 A 主题分出来 → 写在 B 档案的 frontmatter 顶部独立字段，显眼
- **引用**（撞到/提到）：A 主题做的时候撞到了 B 主题 → 藏在 timeline 文本里的 wikilink，不打扰主线

**为什么这么分**：派生少（一主题最多 1-2 个父级）→ Obsidian 关系图主干清晰；引用多但属于细节 → 不污染主图。

### Single Source of Truth
- **Model**（KeyPulse 内部数据，不依赖 Obsidian）：`~/.keypulse/anchors.json`（从现有 `weekly-anchor.json` 升级扩展）
- **View**（可重建）：`anchors/*.md` 由 model 渲染。误删可重生成。未来换 sink 不动 model。

---

## anchor 档案文件格式

```markdown
---
anchor_id: v3-rollout
display: V3 上线
started: 2026-05-12
last_active: 2026-05-20
state: active
derived_from: [[v2-stable]]
---

## Timeline
- 2026-05-12 立项 → [[2026-05-12]]
- 2026-05-15 跑通 smoke50，撞到 [[backfill-cost-bug]] → [[2026-05-15]]
- 2026-05-20 上线 → [[2026-05-20]]
```

**Timeline 行格式**：`- YYYY-MM-DD <一句话摘要> → [[YYYY-MM-DD]]`
- 摘要来自当日 topic.narrative 第一句话或 topic.display
- 摘要里可以含 `[[other-anchor]]` 引用（自动转换）
- 同 anchor_id + 同 date 去重，重跑幂等

---

## 数据流

### daily orchestrator 跑完后
1. 读当日 topics，对每个 `anchor_state in {active, candidate}` 的 topic：
   - 找 `anchors/<slug>.md`，没有就创建（基于 anchor 状态机的 first_seen 信息）
   - 在 Timeline 段落末尾追加：`- {date} {summary} → [[{date}]]`
   - 已存在同 date 行 → 覆盖（处理重跑）
   - 更新 frontmatter `last_active = date`
2. 更新 `~/.keypulse/anchors.json` 对应的 `timeline_entries[]`

### weekly orchestrator 跑完后
1. **派生边检测**：LLM 看本周新出现（first_seen 在本周）的 anchor，判断是否从某老 anchor 分支出来
   - 是 → 写入 model 的 `derived_from`，渲染到档案 frontmatter
   - 否 → 留空
2. **引用边自动转换**：扫每个 anchor 的 timeline summary 文本，遍历所有已知 anchor 的 `display` 字段做**字符串精确匹配**（长度优先，避免子串误命中），命中后转成 `[[<slug>]]` wikilink。**不用 LLM**——纯算法匹配，宁可漏不要错连
3. 重新渲染所有本周变更过的 anchor 档案

---

## 删除清单（一刀砍干净）

这套替换不是"新增"，是"替换 + 减法"。砍掉的部分：

| 砍什么 | 在哪 | 谁写的 |
|---|---|---|
| `events/` 文件夹（一天几百个事件卡 .md）| `~/.keypulse/events/{date}/*.md` | `exporter.py:_build_event_card()` + `write_obsidian_bundle()` |
| `topics/` 文件夹（旧版主题聚合）| `~/.keypulse/topics/*.md` | `daily_orchestrator.py:_upsert_topic()` |
| 日报里"今天的事件卡"区块 | daily markdown 渲染模板 | exporter / daily_summary 渲染逻辑 |
| events / topics 之间的写出 + 互引代码路径 | exporter.py / daily_orchestrator.py | — |

**M4 一次性清理脚本**：
- `rm -rf ~/.keypulse/events/ ~/.keypulse/topics/`
- 改日报模板移除事件卡区块
- 跑近两周历史数据回填新的 anchors/，肉眼验收

**用户感知**：原来日报底下挂一堆 unresolved link、Obsidian 文件树里几千个事件 .md，砍完之后日报回归正常叙事，左侧只多了 `anchors/` 一个文件夹。

**数据安全**：事件原始信息一直在 DB + 日报 narrative 里。`events/` `topics/` 的 .md 是渲染产物，不是 SSOT。

---

## 实施切片

| 步 | 范围 | 验收 |
|---|---|---|
| **M1** | 升级 anchors model：`anchors.json` schema 扩展（`timeline_entries[]`（每条 `{date, summary, daily_ref}`，按 `(anchor_id, date)` 去重）/ `derived_from`），从旧 `weekly-anchor.json` 平迁 | 旧 anchor 数据无损迁到新格式；单元测试通过 |
| **M2** | daily 渲染 anchor note + 停写 events / topics | 跑一天日报：`anchors/*.md` 生成正确，timeline 累加去重；`events/` `topics/` 不再有新文件写入 |
| **M3** | weekly LLM 检测派生边 + 自动转引用 wikilink | 跑一次周报：新 anchor 的 derived_from 填上；timeline summary 里的主题名自动 wikilink |
| **M4** | 清理脚本 + 日报模板改造 + 两周历史回填 | `events/` `topics/` 物理删除；日报正文无事件卡区块；近两周 anchor 档案完整，Obsidian 关系图肉眼可读 |

每片独立可验收。M4 是用户感知最强的一步。

---

## 不动什么

- 现有 daily / weekly pipeline 核心：topic 抽取、anchor 状态机、narrative 一步法
- daily JSON 内部 schema（`events` / `topics` / `events_ref` 等字段保留）
- 不引入新数据库、新插件、新依赖
- 不做 anchor 档案的反向编辑同步（read-only sink）

---

## 黄金验收标准

按 [[feedback_metric_driven_delivery]]：validator pass ≠ 产品 OK，必须人眼读完才能报完成。

- [ ] 翻一个跨度 ≥7 天的 anchor 档案，能一眼看出这件事的演进脉络
- [ ] Obsidian 关系图能直观看出主题之间的派生关系（不糊）
- [ ] 至少 3 个 anchor 之间存在派生边 + 至少 5 处引用边
- [ ] 日报正文从头到尾无 unresolved link
- [ ] `events/` `topics/` 物理删除后，Obsidian 全 vault 无 unresolved link

---

## 风险 / 待观察

1. **anchor 数量爆炸**：如果 anchor 状态机过于敏感，一周生成几十个 anchor → 关系图又糊。M4 验收时观察，如超标则调状态机的"晋升 candidate → active"阈值
2. **LLM 误判派生关系**：宁可漏判不要乱连。M3 prompt 加 ≥0.8 confidence 阈值
3. **跨周 anchor 一致性**：现 `weekly-anchor.json` 按周存。M1 平迁需保证同 slug 跨周延续不丢历史
4. **手动编辑流失**：read-only sink 用户可能不习惯——文档明示，无 UI 提示

---

## 相关 memory / 上下文

- [[project_obsidian_is_process]]：Obsidian 只是当前出口，未来可能换
- [[project_weekly_dual_style]]：周报双 style 渲染共存
- [[feedback_no_dead_rules_on_llm]]：派生检测不上硬规则
- [[feedback_metric_driven_delivery]]：人眼验收

调研 baseline：`weekly_topic_anchor.py` / `daily_orchestrator.py:_upsert_topic` / `exporter.py:_build_event_card` 是本次改造主战场。
