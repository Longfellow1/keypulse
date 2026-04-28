# 噪声清洗 5 层策略

## L0 路径排除
- 黑名单模式：`*backup*`、`*.bak`、`*backup-202*`、`node_modules`、`__pycache__`、`.git/objects`、`Cache`、`.cache`、`.Trash`、`Library/Caches`、`Library/Logs/Diagnostic`、`Crash Reports`、`crashpad`、`*.tmp`、`*.temp`
- 接入位置：`sqlite/jsonl/plist/leveldb/json_files` discoverer 的候选路径阶段

## L1 文件白名单
- sqlite 高敏文件直接阻断：`Cookies`、`Login Data`、`Web Data`、`Network Action Predictor`、`Top Sites`、`Favicons`、`Keychain`
- 系统与旁路文件阻断：`Photos.sqlite`（仅 `~/Pictures/Photos Library.photoslibrary/` 放行）、`chat-merged.db`、`chat-meta-summary*.db`、`History-journal`、`*.sqlite-wal`、`*.sqlite-shm`
- 接入位置：`sqlite` discoverer + `chrome_history/safari_history` read 前

## L2 字段 redact
- 复用 `keypulse/privacy/desensitizer.py`
- `chrome/safari` 读取时先 URL 标准化（去 query/fragment）写入 `artifact`
- `metadata.full_url` 保留完整 URL，但经过 `desensitize`

## L3 内容质量
- 过滤 zsh 短命令黑名单（`ls/cd/clear/...`）
- 过滤 `claude_code` 系统消息（`tool_use_id`/`tool_result`）
- 过滤浏览器空标题 + blank/OAuth 回调页
- 过滤 KeyPulse 自指碎片：`Events/2026-*/片段-*.md`
- 过滤长度 `<4` 且无字母数字文本
- 接入位置：`zsh_history` read 内 + `registry.read_all` 全局流

## L4 时间窗去重
- 键：`(source, intent_normalized, artifact_normalized)`，窗口默认 10 分钟
- 合并策略：保留最早事件，`metadata.dedup_count += 1`
- 例外：`claude_code` 不同 `message_uuid` 不去重；不同 `session_id` 不去重；`git_log` 不同 commit 不去重
- 接入位置：`registry.read_all` 输出尾端

## 噪声回归测试集
- `~/.claude-backup-20260323/...jsonl` 共 21 条路径命中 L0
- `wx.mail.qq.com/home/index` 5 次访问在 L4 合并为 1
- `1777220651` 不再被 commit 正则命中，`11a3a9b`/`bebc1b6` 仍命中
