# Model Gateway Fallback

## 双 backend 设计原理

KeyPulse 的 `ModelGateway` 同时管理两个 backend：

- `cloud`：`openai_compatible`，用于稳定质量与远程模型能力。
- `local`：`lm_studio` / `ollama`，用于离线兜底与隐私优先场景。

网关按 `active_profile` 计算调用顺序；当当前 backend 失败时，自动尝试下一个 backend，直到成功或全部不可用。

## Profile 策略

推荐默认：`cloud-first`

- `cloud-first`：`cloud -> local`
- `local-first`：`local -> cloud`
- `cloud-only`：仅 `cloud`
- `local-only`：仅 `local`

兼容旧配置：`auto`（write 阶段 local-first，其它阶段 cloud-first）、`privacy-locked`（禁用所有 backend）。

## 短路状态机

短路状态存储在 `~/.keypulse/model-state.json`：

- `401/403`：判定认证失败，短路 30 分钟，`reason=auth`
- `Timeout/Connection/URLError/OSError`：判定网络不可达，短路 5 分钟，`reason=network`
- `429`：不短路，直接尝试下一 backend

状态是软短路：

- 到期后自动恢复尝试
- 再次失败会重置短路窗口并增加 `fail_count`
- 成功调用会清空该 backend 的短路状态

并发安全：状态写入使用文件锁 + 原子替换，避免多进程竞争导致 JSON 损坏。

## Keychain 存储机制

云端 API key 不写 `config.toml`，不要求 shell `export`。

配置字段：

```toml
[model.cloud]
api_key_source = "keychain:com.keypulse.model.cloud"
api_key_env = "ARK_API_KEY" # 仅作为兜底
```

解析顺序：

1. 若 `api_key_source` 以 `keychain:` 开头，读取对应 service
2. Keychain 不可用/读取失败时，尝试 `api_key_env`
3. 均无值时，该 backend 视为 unavailable，调用时跳过

Keychain 封装通过 macOS `security` CLI 实现：

- `store_secret`: `add-generic-password ... -U`
- `read_secret`: `find-generic-password ... -w`
- `delete_secret`: `delete-generic-password ...`

非 macOS 环境抛 `KeychainUnavailable`，由调用方处理。

## CLI 用法

### `keypulse model setup`

交互式引导，流程：

1. 配置并测试云端 backend（失败不阻塞保存）
2. 配置本地 backend（可跳过）
3. 选择 profile 策略
4. 写入 `~/.keypulse/config.toml`（原子写）
5. API key 写入 Keychain service `com.keypulse.model.cloud`

设计目标：即使中途网络失败，配置也能先落地，后续修复连通后立即生效。

### `keypulse model status`

输出包含：

- 当前 profile
- `cloud/local` 两个 backend 的 endpoint、认证来源、最近调用
- 每个 backend 的短路剩余时间与冷却窗口
- 当前 fallback 路径说明

## Daemon 集成

新增两个辅助能力：

- `check_daemon_keychain_access()`：检查当前 daemon 上下文是否可用 Keychain（best effort）
- `render_plist_advice()`：发现 `ARK_API_KEY` 仍在 launchd `EnvironmentVariables` 中时，输出迁移建议

`model setup` 完成后会提示：daemon 已可从 Keychain 读 key，无需在 plist 注入 `ARK_API_KEY`。
