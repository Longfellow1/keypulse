# 首次配置 · KeyPulse Setup

KeyPulse 跑起来之后会持续记录你的活动，但**总结叙事 / 生成日报**这一步要靠大模型。所以装完之后第一件事是告诉它"用哪个模型"。

---

## 一行命令

```bash
keypulse setup
```

进交互向导，三选一，**约 2 分钟**完成。

---

## 三种选择，按场景挑

| 选哪个 | 适合谁 | 你需要准备什么 |
|---|---|---|
| **本地 Ollama** | 离线优先 / 不想花钱 / 已经装了 Ollama | 启动 Ollama，至少装一个模型（推荐 `qwen2.5` 或 `llama3.2`） |
| **本地 LM Studio** | 想跑本地、UI 用着舒服 | 打开 LM Studio，加载一个模型，开 server（端口 1234） |
| **云 API** | 要质量、不在乎几块钱 / 月 | 准备一个 Key：豆包（ARK）/ DeepSeek / OpenAI 任选 |

> **混着用也行**：先选云，再补本地，向导会帮你设"优先本地、本地不行回退云"这种策略。

---

## 向导会帮你做的事

1. 探活本地服务（Ollama:11434 / LM Studio:1234），有就直接列模型让你选
2. 云 Key 写进 macOS Keychain，**不会落明文**
3. 选完跑一次连通性测试，**通了才落配置**
4. 落到 `~/.keypulse/config.toml`

---

## 跑完之后

启动 daemon：

```bash
keypulse start
```

如果是 Homebrew 安装，推荐用安装生命周期命令统一管理 launchd：

```bash
keypulse install launchd
```

`.app` / `make install` 现在只作为本地开发打包路径。

验证：

```bash
keypulse healthcheck    # daemon 进程 + 事件流活性
keypulse model status   # 模型后端可用性
```

---

## 没配也能起，但会被提醒

`keypulse start` / `keypulse serve` 启动时会检查模型配置。如果一个可用后端都没有，会在终端打一行红字提醒你跑 `keypulse setup`，**daemon 仍照常启动**——只是叙事 / 报告功能会失败。

---

## 重新配 / 换后端

`keypulse setup` 是**可重入**的，随时再跑一次覆盖。换 Key、换模型、换策略都走它。
