---
capability: L1_entity_extractor
version: v1
model_tier: mini
input_schema: schemas/L1_entity_input.json
output_schema: schemas/L1_entity_output.json
max_tokens: 2000
temperature: 0.2
---

识别用户工作事件中的项目/产品实体。输出 JSON 格式。

## 输入

`date`: 日期
`events`: 事件列表，每条含 `eid`（事件ID）、`c`（内容）、`wu`（知识单元）

## 实体识别原则

1. 自由识别，无预定义清单
2. 从事件内容、窗口标题、工作单元推断项目
3. 实体类型：project / product / tool / feature / system / other
4. 每条事件标主实体，跨实体事件标 needs_review=true 且 confidence < 0.6

## 输出 JSON 格式

```json
{
  "date": "YYYY-MM-DD",
  "entities": [
    {"name": "实体名", "type": "project", "aliases": [], "confidence": 0.95, "evidence_event_ids": ["id1"]}
  ],
  "event_entity_map": [
    {"event_id": "id", "primary_entity": "名称", "secondary_entities": [], "confidence": 0.95, "needs_review": false, "review_reason": ""}
  ],
  "cross_entity_warning": []
}
```

## 要求

- 每条事件必须有 primary_entity
- 跨多实体的事件：confidence < 0.6 + needs_review=true
- 禁止硬规则，仅根据输入识别
