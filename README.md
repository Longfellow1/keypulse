# KeyPulse

> **A local-first Mac companion that turns your daily work traces into private reflective notes.**

KeyPulse quietly observes your work surface — apps, windows, clipboard boundaries, manual notes — forgets sensitive content before it is stored, and writes daily reflection notes into your own Obsidian vault.

Not a productivity dashboard.
Not a screen-time tracker.
Not a cloud surveillance assistant.

A private thinking mirror for knowledge workers.

[English](README.en.md) · [Quick start](#快速开始) · [Obsidian workflow](docs/obsidian-workflow.md) · [Privacy](#隐私一览) · [Roadmap](#当前状态)

---

## Why star this repo?

Star KeyPulse if you care about:

- local-first personal AI
- privacy-preserving activity intelligence
- Obsidian-native daily reflection
- AI tools that help you think instead of replacing thought

---

## 中文介绍

> **KeyPulse·笔友：一个陪你日常输入的笔友 —— 关注你敲下的每一个碎片，但从不记住任何秘密，每天晚上在 Obsidian 给你留一张小纸条。**

KeyPulse 是一个本地优先的 macOS 同行者。它静静地看着你工作的那层表面 —— 你切到哪个 App、粘了一段什么、反复回到哪扇窗 —— 在敏感信息落盘之前就把它忘掉，然后每天在你自己的 Obsidian 库里，给你写一页今天的小结。不给你打分，也不替你做事。它陪你做的只有三件：**记录、整理、进步** —— 让日子不至于流过去就不见了，让底下那些模式，慢慢浮上来。

一句话愿景：*一个安静的伙伴，看见细节，不留秘密，帮你每天更清楚一点。*

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12+-lightgrey.svg)](https://www.apple.com/macos/)
[![Tests](https://img.shields.io/badge/tests-800%2B%20passing-brightgreen.svg)](tests/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## 为什么存在

大多数活动记录工具会掉进三个陷阱之一：

- **仪表盘** —— 漂亮的图表，没人看第二次
- **量化自我** —— "本周 87 分"（所以呢？）
- **AI 助理** —— 它替你做事，于是你不再思考

KeyPulse 不是这三种。它记录碎事 —— 应用切换、剪贴板、窗口标题 —— 不是为了给你打分，而是给一个耐心的观察者足够素材，过几天再回过头跟你说：

> *"这三天你反复回到这个终端 —— 是卡住了，还是其实这才是你真正想做的事？"*

这句话不会 Day 1 就出现。它会在 **Day 5** 出现 —— 当系统已经看了足够久，赢得了开口的资格。

---

## 现在长这样

<p align="center">
  <img src="docs/images/hud-v1.png" alt="KeyPulse HUD" width="320" />
</p>
<img width="450" height="450" alt="图片-2" src="https://github.com/user-attachments/assets/68560f60-46f8-499a-9c25-38965d6dba99" />
<img width="430" height="430" alt="图片-1" src="https://github.com/user-attachments/assets/fb66b0fb-8725-4216-ba20-8edbf7959fa6" />

装上之后，KeyPulse 是一个**住在你菜单栏的小窗口**。

每天打开看一眼：左上"今日重要的事儿"是你早晨给自己定的锚点，下面四个数字告诉你今天积累了多少，再往下三张卡片是它读完今天觉得"值得你回看一下"的片段。底部那行 *"和你一起记录的第 N 天"* —— 它在记你和它一起走了多久。

出问题的时候，它不会丢一个错误码给你看。HUD 顶上会出现一行黄字 + **一颗按钮**："辅助功能权限未授权 [打开设置]" —— 点一下直接弹到该去的系统面板。**修复路径，不是错误堆栈。**

晚上的小结写到你自己的 Obsidian 库里，那是它和你慢慢长出来的另一面。

---

## 它是什么，它拒绝成为什么

| 它**是** | 它**拒绝成为** |
|---|---|
| 一个慢慢摸清你模式的笔友 | 一个为屏幕时间优化的仪表盘 |
| 你思考的镜子 | 一个生产力积分榜 |
| 你的外部记忆 —— 让你能回访过去的自己 | 替你做事的 AI |
| 本地、可审计、在你自己机器上 | 收割你注意力数据的 SaaS |

---

## 它说话的口气

| ✗ 机器腔 | ✓ 笔友腔 |
|---|---|
| `2026-04-21 · 10 events · top theme: terminal` | "今天你盯着终端 11 小时，我有点担心你的腰。" |
| `本周效率 87/100` | "这周你开心的时候比上周少，要不明天换个节奏？" |
| `New theme detected` | "你是不是在想一个新东西？我还没看全，但感觉在酝酿。" |

每一句面向用户的文字都过一道关：**这像笔友写信，还是像管家汇报？** 像后者，就重写。

---

## 里面有什么（工程层）

一些比看起来要难的东西：

### 🧠 人/机 **说话人模型**
每条事件带 `speaker: user`（键盘、剪贴板、手动记录）或 `speaker: system`（窗口标题、辅助功能树、OCR）。日报分两栏渲染 —— **你做了什么** vs. **系统显示了什么** —— 你的声音始终是主线，机器的信号永远不压过你。

### 🔒 隐私写在架构里，不是写在政策文本里
- **不记键盘输入** —— 键盘 watcher 只记**段落与边界**，从不记原始按键。
- **35 个默认黑名单应用** —— 密码管理器、即时通讯、银行 App。事件在写盘之前就丢掉。
- **字段级脱敏** —— 邮箱、Token、API key、卡号在 normalize 阶段就被遮盖。
- **摄像头感知暂停** —— macOS CMIO 激活时（Zoom / FaceTime / 录屏），采集自动暂停。
- **隐私窗口检测** —— Safari/Chrome/Firefox 的无痕窗口通过 AX 标题识别后排除。

### ♻️ 出问题给的是修复路径，不是错误码
后台工具会死、权限会失效、API 会过期。KeyPulse 默认会死，把恢复写进设计：

- **7 个能力域各自体检** —— 辅助功能权限、采集运行时、剪贴板 watcher、健康新鲜度、LLM 后端…… 每 60s 自查一次，互不影响。任何一项异常，HUD 黄条出现"**人话 + 一颗修复按钮**"，不是抛错误码让你 Google。
- **每小时增量 Obsidian sync** —— 基于 cursor，按事件身份去重，**append-safe**：你写过的 `## 今日主线` 叙事和 `## 明天的锚点` 永远不会被覆盖。
- **launchd 托管四件套** —— daemon / healthcheck / 每小时 sync / 每日 sync。开机自启，崩了自动拉起。

### 📝 Obsidian 作为阅读面
日报、事件卡、主题卡。反馈以 checkbox 内嵌（`- [ ] 确认 [ ] 否掉 [ ] 拆分`），watcher 同步回反馈库 —— 零切换，**报告本身就是反馈表**。

KeyPulse 不想替代你的 PKM 系统。它只把每天的工作痕迹写回你已经在用的知识系统：

- 生成纯 Markdown，写入 `Daily/`、`Events/`、`Topics/`、`Anchors/`
- 使用 YAML frontmatter，方便 Obsidian Properties / Bases / Dataview 读取
- 保留双链、标签和 checkbox 反馈，让日报继续长成图谱
- 不需要云同步；如果你使用 Obsidian Sync / iCloud / Git，那是你自己的选择

完整工作流见 [docs/obsidian-workflow.md](docs/obsidian-workflow.md)。

### 🧪 质量保障
一套**黄金集**（Golden Set）标注日记录，作为叙事 pipeline 的回归基线，防止调阈值时静默退化。800+ 个测试全绿 —— 包括那次把 2341 条用户事件错标成系统事件的迁移事故的回归覆盖，以及 capability 框架的架构不变量测试。

---

## 架构一览

```
┌──────────────────────────────────────────────────────────────┐
│  采集层                                                       │
│  • 窗口 / AX 文本 / OCR   (speaker: system)                   │
│  • 键盘段落 / 剪贴板 / 手动记录  (speaker: user)              │
│  • 摄像头监控 → CMIO 激活时暂停 AX+OCR                        │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  隐私层                                                       │
│  • 黑名单（bundle ID + glob） • 字段脱敏                      │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  存储 — SQLite（raw_events, sessions, FTS5）                  │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Pipeline                                                     │
│  • Session 化 → 工作块聚合（人机双栏）                        │
│  • 主题抽取（只看 user 事件）                                 │
│  • 叙事渲染（LLM + 确定性兜底）                               │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  呈现面                                                       │
│  • Obsidian vault（Daily / Events / Topics / Dashboard）      │
│  • 菜单栏 HUD（WKWebView · 今日重点 + 异常即修复入口）         │
│  • CLI（timeline / search / stats / export）                  │
└──────────────────────────────────────────────────────────────┘

       ↑ Capability 自检层（60s 一轮，7 个能力域各自体检）
       ↑ launchd 编排四件套：
         daemon  •  healthcheck (10m)  •  obsidian-sync-hourly  •  obsidian-sync-daily
```

---

## 快速开始

三步装完，之后它就一直在那。

```bash
# 1. 装 — Homebrew 安装 CLI，并由 KeyPulse 初始化 runtime
curl -fsSL https://raw.githubusercontent.com/Longfellow1/keypulse/main/install.sh | bash

# 或者手动：
brew tap Longfellow1/keypulse
brew install keypulse
keypulse install init

# 2. 配 — 三选一交互向导（约 2 分钟）
keypulse setup
#   ├─ 本地 Ollama  ── 离线优先 / 不想花钱
#   ├─ 本地 LM Studio ── 想跑本地、UI 用着舒服
#   └─ 云 API       ── 豆包 / DeepSeek / OpenAI 任选，Key 写进 Keychain

# 3. 授权 —「辅助功能」+「屏幕录制」（系统设置里勾一下 KeyPulse）
#    HUD 黄条会告诉你下一步该点哪
```

装完它就常驻菜单栏。重启电脑会自动起来，崩了 launchd 帮你拉起来。**一天后**，打开你的 Obsidian 库看 `Daily/<今天>.md`。

> **没配模型也能起**——daemon 照常采集，只是叙事 / 日报先按下不表，等你想起来跑 `keypulse setup` 就接上。

### 常用命令

| 命令 | 功能 |
|---|---|
| `keypulse timeline --today` | 查看今日会话 |
| `keypulse search "<query>"` | 全文搜索你的记忆 |
| `keypulse obsidian sync --incremental` | 把新事件追加到今日报告（每小时自动） |
| `keypulse obsidian sync --yesterday` | 生成昨天的完整叙事（每日 09:05 自动） |
| `keypulse healthcheck` | 原子健康报告（launchd 每 10 分钟自动跑） |
| `keypulse purge --app Slack --confirm` | 彻底删除某个应用的全部数据 |

完整命令参考：`keypulse --help`。安装细节看 [docs/homebrew-install.md](docs/homebrew-install.md)，首次配置看 [docs/setup-onboarding.md](docs/setup-onboarding.md)。

---

## 隐私一览

| 会被记录 | 永远不会被记录 |
|---|---|
| 应用名、窗口标题（黑名单之外） | 原始按键 |
| 剪贴板文本（≤ 2000 字符，去重） | 密码管理器 / 即时通讯 / 银行 App 里的任何内容 |
| 手动 `keypulse save` 笔记 | 摄像头激活期间的内容 |
| 会话边界、空闲时段 | 隐私 / 无痕浏览窗口 |

全部数据保存在你自己机器上的 `~/.keypulse/keypulse.db`。无云端、无埋点、无网络请求 —— 导出必须你显式执行。

---

## 产品哲学：三条红线

每个功能上线前问三句：

1. 这个设计是在**增加关系**，还是只在增加**功能**？
2. 这段文字像**笔友**，还是像**管家**？
3. 用户看到会觉得"它懂我"，还是"它在记账"？

任何一条答错，就该重设计。

---

## 要去哪里 —— 从笔记走到方法论

今天，KeyPulse 每天给你写一张小纸条。**它真正想去的地方**是更大的东西：**读懂你和它一起在 Obsidian 里慢慢长出来的那张图，帮你把「模式」变成「方法」。**

Obsidian 不只是一个笔记本，它是一张图。当日报、主题卡、事件卡不断累积、互相链接，它们慢慢构成的不是「你做过什么」，而是**「你是怎么工作的」**。KeyPulse 未来会坐在这张图的上面，像一个有耐心的读者 —— 注意那些反复出现的边、提出关于"你怎么做事"的小假设，然后邀请你去命名它们。

方向上，有点像 Karpathy 提过的 *auto-research*，但不是"让模型替你做研究"，而是**"让模型给你提出关于你自己的小问题，由你来回答"**。时间久了，你的答案本身，就成了产出。

具体会长成这样：

| 你做了什么 | KeyPulse 最终会说 | 它会变成什么 |
|---|---|---|
| 这周五次会话都反复打开同一个文件 | *"这更像是你**思考**的地方，而不是你**改代码**的地方。"* | 你工作流里一个被命名的位置 |
| 每次都是 docs → 终端 → 编辑器 的顺序 | *"这可能是你进入深度工作的热身仪式。"* | 一个你可以保留、打破、或进一步研究的模式 |
| 每次 debug 结束都会把一段复制到小本本里 | *"你好像在把 fix 提炼成 lesson —— 要不要为这一类给一个方法论文件？"* | 一个**属于你自己**的方法论库，肉眼可见 |

终态：KeyPulse 不只是帮你记住你做过什么，它帮你看见**你是怎么把事情做好的**，然后把这件事变成一个你可以回看、甚至可以分享的东西。

不是通用的效率 tips。是一座**由你命名、由你塑造、和你一起长**的个人模式库。

> **Obsidian 的图是土壤。KeyPulse 是让这张图变得可读的那个观察者。**

---

## 当前状态

- **平台：** macOS 12+（Apple Silicon + Intel）
- **测试：** 800+ passing（pytest，含 capability 框架架构不变量）
- **安装：** Homebrew 安装 CLI，`keypulse install init` 初始化 runtime + launchd
- **首次配置：** `keypulse setup` 三选一向导，约 2 分钟
- **Roadmap：** 季度 / 年度回忆录式整理 · 多设备合并 · 语音反思对话

KeyPulse 还在早期。笔友现在能跟你聊今天，还不能跟你聊今年 —— 这是下一步。

---

## 许可

Apache 2.0 —— 见 [LICENSE](LICENSE)。

## 作者
**Harland** —— AI Native 产品经理。

- 邮箱: Harland5588@outlook.com

> *KeyPulse 的目标不是让你更高效。*
> *是帮你认清你是谁，你在变成谁。*
