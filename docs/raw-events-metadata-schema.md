# raw_events.metadata_json entities 约定（M0）

`raw_events.metadata_json` 新增统一子对象：`entities`。

```json
{
  "entities": {
    "commit_hash": "21c290d...",
    "file_paths": ["keypulse/hud/summary.py"],
    "urls": ["https://github.com/foo/bar"],
    "session_id": "claude-2026-05-06-abc",
    "app_bundle_id": "com.todesktop.230313mzl4w4u92",
    "named_entities": ["keypulse", "hud", "weekly-report"]
  }
}
```

约定说明：
- `entities` 为对象；字段按可用性写入，不强制全量。
- `urls` 必须是去 query/fragment 的标准化 URL（仅用于实体比对）。
- `named_entities` 来自 `entity_extractor`，去重后写入。
- 浏览器原始 URL 保留在 `metadata_json.url`（若存在）供调试使用，不参与去 query 改写。
