# Entity Extractor Eval — 2026-05

评测样本：7 天
日期：2026-05-19, 2026-05-07, 2026-05-13, 2026-05-21, 2026-05-06, 2026-05-11, 2026-05-20

## 7 天总览

| Date | entity_purity | entity_completeness | overall_quality |
|---|---:|---:|---:|
| 2026-05-19 | 1.000 | 1.000 | 0.980 |
| 2026-05-07 | 1.000 | 0.830 | 0.650 |
| 2026-05-13 | 1.000 | 0.830 | 0.900 |
| 2026-05-21 | 1.000 | 0.963 | 0.920 |
| 2026-05-06 | 0.991 | 0.990 | 0.950 |
| 2026-05-11 | 0.995 | 0.850 | 0.950 |
| 2026-05-20 | 1.000 | 1.000 | 0.980 |

## 2026-05-19 Spotlight

- Entities: KeyPulse, CHAT-0410, 奇趣宝, Agent系统, ChatGPT Atlas, Mindbones, unknown
- News Signal HUD 与 奇趣宝是否分开：是
- News Signal HUD 相关 event_ids: 97799

关键 event mapping:

| event_id | primary_entity | secondary_entities | confidence | needs_review |
|---|---|---|---:|---:|
| 96881 | 奇趣宝 |  | 0.950 | false |
| 97799 | KeyPulse |  | 0.700 | false |
| 96362 | KeyPulse |  | 0.950 | false |
| 96364 | Agent系统 |  | 0.900 | false |
| 96374 | KeyPulse |  | 0.850 | false |
| 96375 | KeyPulse |  | 0.800 | false |
| 96387 | CHAT-0410 |  | 0.900 | false |
| 96414 | KeyPulse |  | 0.950 | false |
| 96518 | CHAT-0410 |  | 0.950 | false |
| 96519 | KeyPulse |  | 0.950 | false |

## Cross-Entity Warning 全局统计

- extractor 报告数量: 67
- judge 真实跨实体数量: 8
- 重合数量: 7
- 重合率: 0.103
- precision: 0.104
- recall: 0.875

## Per-Entity 聚合

| Entity | Days | Avg Purity | Avg Completeness |
|---|---:|---:|---:|
| Agent系统 | 1 | 1.000 | 1.000 |
| Assistant | 1 | 1.000 | 1.000 |
| CHAT-0410 | 3 | 1.000 | 1.000 |
| C_Agents | 1 | 1.000 | 1.000 |
| Career_harness | 1 | 1.000 | 1.000 |
| Carmind_code | 1 | 1.000 | 0.830 |
| Chat Agent | 1 | 1.000 | 0.670 |
| ChatGPT Atlas | 1 | 1.000 | 1.000 |
| CorpusFlow | 3 | 1.000 | 0.910 |
| GitNexus | 2 | 1.000 | 1.000 |
| KeyPulse | 6 | 0.987 | 0.970 |
| Lark Helper | 1 | 1.000 | 1.000 |
| Log_analysis | 1 | 1.000 | 0.800 |
| Longfellow1 | 1 | 1.000 | 1.000 |
| Mindbones | 2 | 1.000 | 1.000 |
| RAGFlow | 1 | 1.000 | 0.667 |
| RAG项目 | 1 | 1.000 | 1.000 |
| finance | 1 | 1.000 | 1.000 |
| keypulse | 1 | 1.000 | 1.000 |
| migration_package | 1 | 1.000 | 1.000 |
| my-slide | 1 | 1.000 | 1.000 |
| unknown | 4 | 1.000 | 0.600 |
| 个人事务 | 1 | 1.000 | 1.000 |
| 奇瑞股价分析 | 1 | 1.000 | 1.000 |
| 奇趣宝 | 2 | 1.000 | 1.000 |
| 工具使用 | 1 | 1.000 | 1.000 |
| 座舱实时语音 Agent 产品化（奇瑞） | 1 | 1.000 | 1.000 |
| 比赛项目 | 1 | 1.000 | 1.000 |
| 泰晶科技分析 | 1 | 1.000 | 1.000 |
| 简历准备 | 1 | 1.000 | 1.000 |

## 失败 Case 列表

阈值：entity_purity < 0.7 或 entity_completeness < 0.7

| Date | Entity | Purity | Completeness | Wrong Event IDs | Missing Event IDs |
|---|---|---:|---:|---|---|
| 2026-05-07 | RAGFlow | 1.000 | 0.667 |  | 28141 |
| 2026-05-07 | unknown | 1.000 | 0.000 |  |  |
| 2026-05-11 | unknown | 1.000 | 0.400 |  | 88503, 88556, 88557 |
| 2026-05-13 | Chat Agent | 1.000 | 0.670 |  | 53921, 89898, 54038, 54044 |
