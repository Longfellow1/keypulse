# Penpal

> **给你所有 AI 共用的本地记忆。** Penpal 在你的 Mac 上记下你看过、写过、问过的东西，通过 MCP 交给 Claude Code、Codex、Cursor 查询，每条结果都能打开出处，每个 AI 能看到什么由你授权。

[English](README.en.md) · [快速开始](#快速开始) · [记什么不记什么](#记什么不记什么) · [现在做到哪](#现在做到哪)

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12+-lightgrey.svg)](https://www.apple.com/macos/)
[![Tests](https://img.shields.io/badge/tests-1000%2B%20passing-brightgreen.svg)](tests/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## 它解决什么

你在 Claude Code 里问："上周我看过一个讲 MCP 鉴权的网页，在哪？" 它答不上来。它没见过你的浏览器；就算让它跑 bash 去翻，浏览器历史 90 天后会过期，剪贴板、切过的窗口、在网页输入框里打过的字，本来就没落盘，事后谁也找不回。

装了 Penpal 之后，同一个问题，AI 调用 `search_context`，返回那条网页记录、你当时在哪个 App 里、前后在做什么，再用 `get_evidence` 打开原始出处，你自己核对是不是它。

Penpal 不做聊天入口，也不内置大模型。你继续在自己常用的 AI 里提问，Penpal 只负责"记得"和"给得准"。

## 为什么不直接让 AI 用 bash

AI 自己能读本机文件，只给已有文件建个索引，没必要单独装一个工具。Penpal 存在的理由只有下面三条，每个功能都要落在其中一条上，并且在对照评测里比"AI + bash"答得好：

| | AI + bash | Penpal |
|---|---|---|
| **本来不落盘的信息**：窗口切换、剪贴板、网页里打过的字 | 找不回 | 采集时就记下 |
| **超过来源保留期的记录**：Claude Code 会话约 30 天、Chrome 历史约 90 天 | 过期就没了 | 入库后一直保留，直到你删 |
| **受控访问** | 给 AI bash 等于开放整盘读取；Claude Desktop、沙箱里的 Codex 根本没有 bash | 按 AI 单独授权、按分类限定范围、入库前脱敏、可撤销、每次查询有记录 |

## 两档安装，按需选

| | CLI（必装的最小集） | 桌面 App（可选，尚未发布） |
|---|---|---|
| 给谁 | 只用终端的开发者 | 想直观看采集状态、做管理的人 |
| 包含 | 后台采集 + MCP 服务 + 全部控制命令 | 菜单栏控制中心：状态、来源开关、AI 授权、删除 |
| 核心能力 | 装好就能在 AI 里用全部核心能力 | 不多不少，同一套核心换一种用法 |

App 能做的事，CLI 都能做。只装 CLI 也不会"坏了没人知道"：采集停了、磁盘满了、权限丢了，AI 下次查询时会在结果里收到提醒并转告你。

## 快速开始

```bash
# 1. 安装 CLI，初始化运行环境，注册开机自启的后台服务
curl -fsSL https://raw.githubusercontent.com/Longfellow1/keypulse/main/install.sh | bash
# 或手动：brew tap Longfellow1/keypulse && brew install penpal && penpal install init

# 2. 检查系统权限：缺哪个、该给哪个程序授权、去哪个设置面板，doctor 都会列出来
penpal doctor

# 3. 授权一个 AI，并按提示把配置加进去（命令会直接打印 Claude Code / Codex / Cursor 的配置）
penpal mcp grant claude --buckets Work,Personal
```

然后在 Claude Code 里正常提问，比如"我昨天下午在研究什么"。

## AI 能用的工具

| 工具 | 做什么 |
|---|---|
| `search_context` | 按关键词、时间、来源搜索，返回本次搜了哪些范围、每个关键词有没有命中 |
| `get_timeline` | 按时间浏览某段时间做了什么 |
| `get_evidence` | 打开某条结果的原始出处 |
| `rate_result` | 对结果打分，用来改进排序 |

只读。查到的内容会进入该 AI 的对话，可能发送到它的云端服务。授权时按分类（Work / Personal / Private / Financial）限定，`penpal mcp audit` 能看到谁在什么时候查过、结果有几条，不记录查询内容。

## 记什么不记什么

**会记录**（每个来源都能单独关）：

- 实时采集：前台窗口切换、剪贴板、当前网页地址、键盘文字片段
- 导入：Chrome / Safari 历史、Claude Code 与 Codex 会话、终端命令历史、Git 提交、Spotlight

**不会记录或默认排除**：

- 密码管理器等默认排除名单里的 App 不记录；无痕浏览窗口只记应用和时间，不记内容；摄像头开启期间暂停屏幕文字采集
- 银行、支付类内容自动归入 Financial 分类，没有授权 Financial 的 AI 查不到
- 聊天 App（微信、Slack、飞书等）默认不采集；单独开启后进入 Private 分类，只有授权了 Private 的 AI 能查到
- 键盘：不存逐个按键，只存过滤后的文字片段；默认不进 AI 搜索，AI 明确指定来源时才查
- 邮箱 @ 前面打码、域名保留，令牌、密钥、卡号入库前遮盖

数据只在你本机 `~/.penpal/`，没有云端、没有埋点。不会自动删除，删除由你决定，删的时候原文、索引、出处、相关查询记录一起清掉。

## 日常控制

| 要做的事 | 命令 |
|---|---|
| 看状态、查健康 | `penpal status`、`penpal health`、`penpal doctor` |
| 暂停、继续（重启后仍保持暂停） | `penpal pause`、`penpal resume` |
| 开关某个来源 | `penpal source list`、`penpal source disable keyboard_chunk` |
| 放行某个聊天 App | `penpal privacy chat-apps allow Slack` |
| 管 AI 授权 | `penpal mcp list`、`penpal mcp revoke <client>`、`penpal mcp rotate <client>` |
| 删除数据（先预览数量） | `penpal purge --source chrome_history --dry-run` |

完整参考：`penpal --help`。

## 现在做到哪

- **召回准确率**：作者在本机真实数据上建了 50 题评测集，其中约三分之一只给部分线索。由独立模型作答、自动判分，三轮平均答对 46.7 题（93%），越权返回 0 次。评测集含个人数据，不公开。
- **已可用**：采集、导入、脱敏、分类授权、搜索、出处、删除、暂停与来源开关、无界面故障提醒。
- **还没做到**：
  - 键盘片段目前只可靠覆盖英文，中文输入法上屏文字的采集还在试验。
  - 只支持 macOS。
  - AI 只能读，不能写回长期记忆。
  - 桌面 App 未发布。

## 许可

Apache 2.0，见 [LICENSE](LICENSE)。

## 作者

**Harland**，AI Native 产品经理。邮箱：Harland5588@outlook.com
