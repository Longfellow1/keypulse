# Entity Extractor Eval — 2026-05

评测样本：7 天
日期：2026-05-19, 2026-05-07, 2026-05-13, 2026-05-21, 2026-05-06, 2026-05-11, 2026-05-20

## 7 天总览

| Date | entity_purity | entity_completeness | overall_quality |
|---|---:|---:|---:|
| 2026-05-19 | 1.000 | 1.000 | 1.000 |
| 2026-05-07 | 0.875 | 0.875 | 0.850 |
| 2026-05-13 | 1.000 | 1.000 | 1.000 |
| 2026-05-21 | 0.950 | 0.910 | 0.950 |
| 2026-05-06 | 1.000 | 1.000 | 0.980 |
| 2026-05-11 | 1.000 | 1.000 | 0.850 |
| 2026-05-20 | 1.000 | 1.000 | 0.980 |

## 2026-05-19 Spotlight

- Entities: KeyPulse, CHAT-0410, 奇趣宝, Mindbones, CorpusFlow, Carmind, unknown
- News Signal HUD 与 奇趣宝是否分开：是
- News Signal HUD 相关 event_ids: 97799

关键 event mapping:

| event_id | primary_entity | secondary_entities | confidence | needs_review |
|---|---|---|---:|---:|
| 96881 | 奇趣宝 |  | 0.900 | false |
| 97799 | unknown |  | 0.400 | false |
| 96362 | KeyPulse |  | 0.950 | false |
| 96364 | unknown |  | 0.400 | false |
| 96374 | KeyPulse |  | 0.800 | false |
| 96375 | KeyPulse |  | 0.800 | false |
| 96387 | CHAT-0410 |  | 0.850 | false |
| 96414 | KeyPulse |  | 0.950 | false |
| 96518 | CHAT-0410 |  | 0.850 | false |
| 96519 | KeyPulse |  | 0.950 | false |

## Cross-Entity Warning 全局统计

- extractor 报告数量: 2
- judge 真实跨实体数量: 4
- 重合数量: 2
- 重合率: 0.500
- precision: 1.000
- recall: 0.500

## Per-Entity 聚合

| Entity | Days | Avg Purity | Avg Completeness |
|---|---:|---:|---:|
| Agent_Runtime_PoC | 1 | 1.000 | 1.000 |
| CHAT-0410 | 3 | 1.000 | 1.000 |
| C_Agents | 1 | 1.000 | 1.000 |
| Career_harness | 1 | 1.000 | 1.000 |
| Carmind | 1 | 1.000 | 1.000 |
| Carmind_code | 1 | 1.000 | 0.800 |
| Chat Agent | 1 | 1.000 | 1.000 |
| CorpusFlow | 6 | 1.000 | 1.000 |
| Finance | 1 | 1.000 | 1.000 |
| GitNexus | 2 | 1.000 | 1.000 |
| KeyPulse | 7 | 1.000 | 1.000 |
| Log_analysis | 1 | 1.000 | 0.800 |
| Longfellow1 | 1 | 1.000 | 1.000 |
| Mindbones | 2 | 1.000 | 1.000 |
| RAGFlow | 1 | 1.000 | 1.000 |
| corpusflow-deploy | 1 | 1.000 | 1.000 |
| migration_package | 1 | 1.000 | 1.000 |
| my-slide | 1 | 1.000 | 1.000 |
| unknown | 4 | 0.625 | 0.625 |
| 奇趣宝 | 2 | 1.000 | 1.000 |
| 座舱实时语音 Agent 产品化 | 1 | 1.000 | 1.000 |
| 泰晶科技 | 1 | 1.000 | 1.000 |

## 失败 Case 列表

阈值：entity_purity < 0.7 或 entity_completeness < 0.7

| Date | Entity | Purity | Completeness | Wrong Event IDs | Missing Event IDs |
|---|---|---:|---:|---|---|
| 2026-05-07 | unknown | 0.000 | 0.000 |  |  |
| 2026-05-21 | unknown | 0.500 | 0.500 |  |  |
