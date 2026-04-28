# KeyPulse 数据源架构重构方案

**起草日期**：2026-04-28
**作者**：Harland + Claude（产品讨论纪要 + 工程规划）
**状态**：方向确认，进入 Sprint 0

---

## 0. 缘起

2026-04-28 验收 Daily 报告体验后，确认 KeyPulse 当前距离"产生用户价值"还有不小差距。对照 Claude 回忆功能的产出（按事件级问题/做法/结论的高密度叙事），我们识别出**根本差距**：

| 维度 | 当前 KeyPulse | Claude 回忆（金标准） |
|---|---|---|
| 报告单位 | 动机分类（维护/理解/创造 7 桶） | 具体的事（服务器调试 / 选型决策 / 求职准备）|
| 证据形态 | 屏幕表象（"在终端切换目录"） | 问题→做法→结论→产物 |
| 数据源 | 行为流（OCR / AX / IME / 窗口） | 结构化对话流 |
| 跨日整合 | 无（每天独立） | 跨日聚合（一件事跨多日）|

**核心结论**：差距不在 prompt tuning，在**数据源的语义结构**。屏幕事件流是无结构原料，七桶动机分类是它能产出的 v0 算法天花板。

---

## 1. 核心判断

### 判断 1：屏幕路线降级，本地数据库直读路线为主路

绝大多数有价值的数据**根本不在屏幕上才有**——它们在应用的本地数据库 / API 里：

- 跟 Claude Code 的对话 → `~/.claude/projects/<path>/<uuid>.jsonl`
- 今天的 git commit → `git log --since=00:00`
- 浏览器看的页面 → Chrome SQLite `History`
- iMessage / Mail → 本地 SQLite

数据库直读路线相对屏幕路线的优势：

| 维度 | 屏幕路线（OCR/AX/IME） | 本地数据库直读 |
|---|---|---|
| 信噪比 | 低（屏幕表象、需要 redact） | 高（应用自己存的就是"事情"）|
| 隐私 | 截屏 / 键入碎片，重 | 本地不离机，可解释 |
| 工程量 | 高（pipeline、过滤、去重） | 低（read sqlite / parse jsonl） |
| 跨人群 | 都得跑 OCR | 每人接自己用的应用 |

OCR/AX/IME 不废，**降级为兜底层**——只在数据库直读未覆盖时（大象/如流等内部 IM、PDF 阅读、白板软件）使用。

### 判断 2：跨源事件关联是 v1 算法核心

光接金矿不够，**真正的产品价值在跨源关联**：

> 11:08 跟 Claude Code 说"修 timeline 段"
> 11:23 git commit 11a3a9b
> 11:45 在 Obsidian 写下这件事
> 12:01 跟 Harland 沟通"打个补丁"

这是**一件事**。如果各源独立列证据，Daily 会写成 4 条。

需要做：
1. **实体抽取**：从 SemanticEvent 提取关键名词（commit hash / file path / PR / issue / URL / 人名 / 项目名）
2. **跨源聚类**：同实体 + 时间窗共现 → "事情"实体
3. **LLM 描述事情**（不是描述事件流）

"动机分类" 是 v0 算法，"跨源事件关联" 是 v1，必须做。

### 判断 3：硬编码每个金矿会爆，要做接入器框架

接入 N 个源不能每个写一坨硬编码。应抽象 `DataSource` 接口，每个 plugin 自己负责"识别 + 接入"一体化：

```python
class DataSource:
    def discover(self) -> list[DataSourceInstance]:
        """扫本机找到这个数据源的具体实例。本机没装就返回空，不报错。"""
    def read(self, since, until) -> Iterator[SemanticEvent]:
        """读出统一格式的事件流。"""
```

**框架启动时跑所有 plugin 的 `discover()`，结果驱动**——本机长什么样就接什么样，不写死任何用户名 / 路径。

### 判断 4：三层采集策略

不是"接 N 个具体源"，是**分层覆盖**：

#### L1：macOS 系统级广播（一个 FDA 授权解决一半）

| 数据源 | 覆盖什么 |
|---|---|
| `knowledgeC.db` | 应用使用时长、文档打开关闭、屏幕锁定、Siri 查询、通知 |
| Spotlight / mdfind | 全文索引、最近修改文件 |
| Screen Time API | 系统级应用使用统计（比 KeyPulse 自己窗口聚焦更准）|
| 统一日志 `log show` | 应用启动 / 失败、登录、网络 |
| Quick Look cache | 用户预览过哪些文件 |

#### L2：本地结构化文件扫描（半自动金矿发现）

不只扫 SQLite，还包括 JSONL 和 plist：

- **SQLite 发现器**：扫 `~/Library/{Application Support,Containers}/**/*.{db,sqlite}`，magic bytes 验证，schema 启发式（表名含 messages/chats/history/sessions/conversations/events 标记为高信号候选）
- **JSONL 发现器**：扫 `~` 找 `*.jsonl`，按头部内容识别（含 user/assistant 字段的疑似对话）
- **Plist 发现器**：扫 `~/Library/Preferences/*.plist`，读应用最近文件 / 最近搜索

**扫描只读 schema，绝不读 row**——row 必须由专用 adapter 主动 query。这是底线。

#### L3：屏幕路线（兜底）

OCR / AX / IME / 窗口标题。降级后可以更激进过滤（IME 完全砍 / AX 只留高信噪比）。

### 判断 5：隐私分层

不是所有金矿能默认读。三档：

| 档位 | 例子 | 策略 |
|---|---|---|
| 🟢 绿区 | git / Claude Code / 浏览器历史 / IDE / 笔记 | 默认采集（本机自己产物，不涉他人）|
| 🟡 黄区 | iMessage / 钉钉 / 飞书 / 邮件 | 首次询问后启用 |
| 🔴 红区 | 微信 / QQ / 工作群组 | 用户授权 = 全读，不授权 = 整个源不接（不做"只读单侧"）|

**红区采集决策**：要做就做透，不做半截。微信不授权 → 整个微信不接，授权 → 完整对话流（含双方）。

### 判断 6：dogfood 优先

接入顺序按"作者本机当前用了多少"，不按"市场覆盖率"。先把作者桌面变成"全金矿覆盖"，让 Daily 真正能产出 Claude 回忆那种叙事，再谈推广。

---

## 2. 核心抽象

### SemanticEvent

每个数据源产出统一格式：

```python
@dataclass
class SemanticEvent:
    time: datetime           # 事件发生时间
    source: str              # 数据源标识（"claude_code"、"git_log"）
    actor: str               # 主体（"Harland"、邮件发件人等）
    intent: str              # 意图描述（LLM 抽取或源本身提供）
    artifact: str            # 产物（commit hash / 文件路径 / URL / 消息 ID）
    raw_ref: str             # 原始引用（用于回溯 / 审计）
    privacy_tier: str        # "green" / "yellow" / "red"
    metadata: dict           # 扩展字段
```

### DataSource 接口

```python
class DataSource(ABC):
    name: str                # 唯一标识
    privacy_tier: str        # "green" / "yellow" / "red"
    liveness: str            # "always" / "app_running" / "after_unlock"

    @abstractmethod
    def discover(self) -> list["DataSourceInstance"]:
        """扫本机找具体实例。本机没装返回空。"""

    @abstractmethod
    def read(self, instance, since, until) -> Iterator[SemanticEvent]:
        """读出事件流。"""
```

### Liveness 调度（关键约束）

不同金矿采集时机不一样：

| Liveness | 含义 | 例子 |
|---|---|---|
| `always` | 任意时刻可读 | git log / 本地 jsonl / 不锁库的 sqlite |
| `app_running` | 应用运行时数据库被锁，要 copy 副本 | Chrome 历史 |
| `after_unlock` | 必须从内存抓加密 key | 微信 / 钉钉 |

后续 sprint 需要扩展 daemon 调度器：监听 macOS `NSWorkspace.didLaunchApplicationNotification`，应用启动事件触发对应金矿采集。

---

## 3. Sprint 里程碑

### Sprint 0｜架构骨架（1 天 / Codex MCP gpt-5.3-codex）

**交付代码**：
```
keypulse/sources/
├── types.py        # SemanticEvent + DataSource 接口
├── registry.py     # 注册表 + 启动 discover 流程
├── discover.py     # CLI 入口
└── plugins/
    └── git_log.py  # 第一个真实 plugin

tests/
├── test_sources_types.py
├── test_sources_registry.py
└── test_sources_git_log.py

keypulse/cli.py     # +1 group: sources
```

**可运行命令**：
```bash
keypulse sources list
keypulse sources discover
keypulse sources read --source=git_log --since=2026-04-28 [--json]
```

**验收点**：
1. `discover` 输出含本机所有 git 仓库
2. `read` 至少返回今天 commit 11a3a9b 这条 SemanticEvent
3. 机器没 git → 不报错，返回空
4. 路径不 hardcode 用户名，用 `Path.home()` / 环境变量
5. pytest 覆盖：discover mock 文件系统 + read 字段完整性

**故意不做**：不接日报渲染 / 不入库 / 不接其他 plugin。专注架构干净。

---

### Sprint 1｜通用发现器 + 精读适配器（3-5 天 / Codex MCP gpt-5.3-codex）

**Part A：三类通用发现器**：
```
keypulse/sources/discoverers/
├── sqlite.py       # 扫 ~/Library 找 sqlite，schema 启发式
├── jsonl.py        # 扫 ~ 找 jsonl，按头部内容识别
└── plist.py        # 扫 ~/Library/Preferences 读最近文件
```

SQLite 发现器规则：
1. glob `~/Library/{Application Support,Containers}/**/*.{db,sqlite}`（限定深度避免爆炸）
2. magic bytes 验证 + 读 schema
3. 表名命中 `{messages, chats, history, sessions, conversations, events}` → 高信号候选
4. **绝不读 row**——只读 schema
5. 输出 `{path, app_name, hint_tables, schema_signature}`

**Part B：精读适配器**：
```
keypulse/sources/plugins/
├── git_log.py          # S0 已有
├── claude_code.py      # ~/.claude/projects/**/*.jsonl
├── codex_cli.py        # ~/.codex/sessions/（先调研路径）
├── chrome_history.py   # Chrome History sqlite
└── zsh_history.py      # ~/.zsh_history
```

每个 adapter 声明它"认领"哪些 schema_signature。SQLite 发现器扫到的库已被 adapter 认领 → 走 adapter 深度抽取；未认领 → 留候选清单。

**discover 输出三段**：
```
✅ 已识别（精读）：N 个源
🟡 候选金矿（未识别，需用户确认）：N 个
❌ 未发现（本机未装）：列出 plugin name
```

**验收点**：
1. discover 输出至少 5 个已识别源 + N 个候选金矿
2. read 一次性吐出当天所有源的 SemanticEvent，时间排序
3. 本机没装的源不报错，列在"未发现"
4. 候选金矿**不读 row**（grep 测试：候选输出无 row 内容）
5. 跨机可用——不该 hardcode 任何 `Harland`

**S1 决策记录**：
- SQLite 扫描深度：首次扫 `~/Library` 全量（30-60 秒），后续增量
- 候选金矿确认方式：S1 先 CLI（`keypulse sources approve <id>`），HUD 集成放 S4

---

### Sprint 2｜跨源关联算法 v1（3-5 天）

- **实体抽取器**：commit hash / 文件路径 / PR-issue 号 / URL / 人名 / 项目名
- **事件聚类**：同实体 + 时间窗共现 → "事情"实体
- **LLM prompt 重写**：从"概括事件流"切换到"描述 N 件事，每件给问题/做法/结论/产物"

**验收点**：Daily 写出 "修了 timeline 段隐私回归（commit 11a3a9b、5 条相关对话、3 个文件改动）" 这种一件事一段的产出。

---

### Sprint 3｜L1 系统级源（2-3 天）

- KnowledgeC.db 接入
- Spotlight / mdfind 时间窗 query
- Screen Time API（如可用）

**验收点**：FDA 授权一次得到 70% 桌面活动覆盖。

---

### Sprint 4｜半自动确认 UI + 候选金矿提升率（3-5 天）

- 候选金矿确认 UI（CLI / HUD）
- 用户挑选要纳入哪些候选
- 已识别 vs 候选转化率统计

**验收点**：扫一次发现 ≥ 10 个候选，用户确认率 ≥ 30%。

---

### Sprint 5｜红区金矿 + liveness 调度（5-7 天）

- daemon 监听 `NSWorkspace.didLaunchApplicationNotification`
- liveness 状态机
- 微信集成（[chatlog](https://github.com/sjzar/chatlog)）

**验收点**：微信启动后 30 秒内自动采集，关闭后停止。

---

### Sprint 6｜OCR/AX/IME 降级 + 整体验收（2-3 天）

- 现有源降级 fallback
- IME / 低信噪比源更激进过滤
- 端到端 Daily 真链路对照 Claude 回忆样本

**验收点**：完整 Daily 跟 Claude 回忆样本对照，主线 / 做法 / 结论三段式落地。

---

## 4. 总周期

3-4 周（含验收 buffer 和 dogfood 调优）。

---

## 5. 角色分工

| 角色 | 职责 |
|---|---|
| Harland（产品负责人）| 每 sprint 末验收，给 dogfood 反馈 |
| Opus（架构 / 决策）| 每 sprint 写 brief、架构评审、对齐方向 |
| Codex MCP（gpt-5.3-codex）| 每 sprint 主力实现 |
| Haiku（侦察）| 调研未知 app 路径、扫 schema、读文件摘要 |

---

## 6. 风险与决策记录

| 决策 | 选择 | 替代方案（已否） | 理由 |
|---|---|---|---|
| 隐私边界 | 红区授权=全读，不授权=不接 | 只读单侧 | 不左不右，用户体验差 |
| 接入顺序 | 通用发现器先于具体 adapter | 硬编码 5 个 plugin 先 | 避免技术债，自适应 |
| 数据源接入策略 | 三层（系统/数据库/屏幕）| 单层 OCR 兜底 | 高 ROI，隐私可解释 |
| 算法演进 | v0 动机分类 → v1 跨源关联 | 继续打磨动机 prompt | 天花板限制 |
| 工程节奏 | B 方向（架构纵深 3-4 周）| A 方向（硬编码快胜 1 周）| 长期主义，不还技术债 |

---

## 7. 关键不变式

下面这些约束在所有 sprint 中必须守住：

1. **不 hardcode 用户名 / 绝对路径**：用 `Path.home()` / 环境变量
2. **discover 失败不爆**：本机没装 = 返回空列表
3. **候选金矿不读 row**：只读 schema，row 必须 adapter 主动取
4. **每个 plugin 独立可测**：mock 文件系统 / mock sqlite
5. **隐私 tier 字段必填**：每个 SemanticEvent 必须有 privacy_tier
6. **跨机可用**：plugin 在新 Mac 上运行不需任何配置

---

## 8. 启动条件

- ✅ 方向 B 确认（长期主义）
- ✅ 跨源关联算法 v1 是 P0
- ✅ 隐私分层"全或无"决策
- ✅ Codex MCP gpt-5.3-codex 派活
- ✅ Sprint 0 brief 见本文档 §3 Sprint 0

下一步：派 Codex MCP 实现 Sprint 0。
