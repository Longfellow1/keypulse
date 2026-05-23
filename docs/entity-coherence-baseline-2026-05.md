# Entity Coherence Baseline — 2026-05

跑期：2026-05-01 ~ 2026-05-23
评测样本：18 篇 daily
平均污染分：0.018
高污染日（pollution_score > 0.5）：0 天

## 污染日详情

### 2026-05-19 — 污染分 0.33

- ✗ 「奇趣宝跨场景服务规划」 confidence=0.90
  - 主实体：奇趣宝（产品项目）
  - 入侵实体：News Signal HUD
  - 污染句：
    - 「17:12提出Agent确认卡片需嵌入对话流程，并规划News Signal HUD按P0/P1/P」
  - 原因：narrative中奇趣宝的具体活动包括明确前端交互设计、提出Agent确认卡片嵌入对话流程；同时出现News Signal HUD的优先级规划活动，两个独立实体各有具体内容，符合污染判定标准。

## 干净日统计

- 共 17 天 pollution_score = 0
- 共 0 天 0 < score ≤ 0.3（轻度）
- 共 1 天 0.3 < score ≤ 0.5（中度）
- 共 0 天 score > 0.5（重度）

## Opus 决策接口

（这一节留给 Opus 决定 Step 2 改算法的力度）
