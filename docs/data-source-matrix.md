# 金矿识别矩阵

## 维度一：存储类型 × 当前覆盖

| 类型 | 格式 | Plugin / Discoverer | 价值密度 |
|---|---|---|---|
| 关系型 | SQLite | `sqlite_discoverer` + `chrome_history/safari_history` 等 plugins | ⭐⭐⭐⭐⭐ |
| KV/LSM | LevelDB | `leveldb_discoverer`（识别 only） | ⭐⭐⭐⭐⭐（待读取） |
| 文档 | Markdown Vault | `markdown_vault` | ⭐⭐⭐⭐ |
| 文档 | JSON | `json_files_discoverer` | ⭐⭐⭐⭐ |
| 日志流 | JSONL | `jsonl_discoverer` + `claude_code/codex_cli` | ⭐⭐⭐⭐⭐ |
| 配置 | plist | `plist_discoverer` | ⭐⭐ |
| 关系型 | IndexedDB | 未接入 | ⭐⭐⭐ |
| 对象数据库 | Realm | 未接入 | ⭐⭐⭐ |
| KV/LSM | RocksDB | 未接入 | ⭐⭐⭐ |
| 文档 | BSON | 未接入 | ⭐⭐ |

## 维度二：字段启发式（金矿信号词）

| 维度 | 关键词 | 可信度 |
|---|---|---|
| 意图文本 | `text, content, body, message, prompt` | high |
| AI 对话 | `role, user, assistant, completion` | high |
| 会话锚 | `session_id, conversation_id` | medium |
| 浏览导航 | `title, url, visit` | high |
| 沟通邮件 | `from, to, sender, subject` | medium |
| 时序锚 | `timestamp, ts, created_at, time` | low（通用字段） |
| 产物 | `path, file, document, project` | medium |
| 状态 | `status, state, action, event` | low |

## 维度三：路线图

- Sprint 0-1（已完成）：`sources` 框架 + git/CLI/browser/history + sqlite/jsonl/plist discover。
- Sprint 1.5（本次）：补齐 `leveldb/json_files/markdown_vault`，并统一字段级启发式（`FIELD_HEURISTICS`）。
- Sprint 2：LevelDB 只读 key-space 结构解析、IndexedDB/Realm/RocksDB discoverer。
- Sprint 3：跨源实体关联（session/commit/path/url）与事件聚类，替代单源平铺。
