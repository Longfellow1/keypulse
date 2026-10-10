# KeyPulse 演进时间线

> 持久战演进记录。每个 milestone 完成后在「已完成」最上面追加一行。
>
> 与 `CHANGELOG.md` 的分工：本文档面向**开发者**看路线和决策；CHANGELOG 面向**用户**看发布。CHANGELOG 当前停在 Swift 时代（2.0.0），Python 重构后未续。

## 项目定位

macOS 上的「产出叙事」工具：把一天/一周的真实工作产出，转成可解释的叙事记录。

## 三条主线

| 主线 | 解决什么 | 关键文档 |
|---|---|---|
| **数据采集（Plan A）** | 屏幕路线降级，本地数据库直读为主，跨源聚类成"一件事" | `docs/data-source-architecture.md` |
| **报告生成（Daily / Weekly）** | 从动机分类升级到主线锚定 + 双层叙事 | `docs/refactor-daily-renderer-unification.md`、`docs/weekly-report-v3-design.md` |
| **产品健康度（M0-M3）** | 交付可靠性 + 自愈序列 + HUD 业务化显示 | M0-M3 commit message `feat(reliability)` |

## 当前阶段

`Plan A 收尾（N1-N4 已交付）+ M0-M3 收口` — 产品从"跑得起来"到"能装能信能日用"。

## 当前进行中

（空）

## Backlog

（空）

## 已完成（倒序，最新在上）

### 2026-05-15 — N5 py2app + macOS 26 GUI abort 防线
- **根因**：py2app 0.28.10 stub 的 `report_error()` 走 `ensureGUI()` → `NSApplication.sharedApplication()` → macOS 26 (Tahoe) 的 `_RegisterApplication` 在 dispatch_once callout 里 abort。任何 bootstrap 错误（缺包、坏 boot）都会被这条 GUI 弹框路径放大成静默 abort，crash log 在 `KeyPulse-2026-05-15-111349.ips`。
- **fix**：preflight 加 `smoke_test_inner_binary()` —— `make install` 阶段实跑 `inner --help`，exit≠0 或 stdout 缺 `Usage:` 都拦下并打印 stderr 尾 8 行
- 真链路：healthy bundle preflight OK；负向 fake bundle 抛 `ImportError` 被 smoke 抓到
- 留作护栏：M0 locale fix 只覆盖一种 bootstrap 失败模式；smoke test 是通用兜底

### 2026-05-15 — Plan A 收尾 N1-N4
- **N1 LevelDB reader** 实现（IndexedDB 金矿读取，approval gating，strings/JSON fallback）
- **N2 Spotlight reader** 实现（`mdfind + mdls` 元数据，content type 黑名单收窄，隐私 roots 收紧，metadata datetime 序列化）
- **N3 sources scheduler** 实现（NSWorkspace 事件触发 + always 轮询 + debounce + 异常隔离）
- **N4 approval HUD UI** 实现（菜单"候选审批 (n)" + NSWindow 列表 + 隐私优先）
- 真链路：Spotlight 29 events / 7d 实测，hud-state `pending_count: 408`，scheduler NSWorkspace event source started
- 修复账本：mdfind query 格式、PyObjC `super().init()`、UTI `public.data` 黑名单过头、隐私路径泄漏 4 个真链路 bug

### 2026-05-15 — M0-M3 产品健康度收口
触发：5/14 silent fail 事故（`UnicodeDecodeError` 让日报缺席 24h，HUD 不报错）。
- **M0** py2app locale 修复（`_force_utf8_locale()` 在所有 keypulse import 之前）
- **M1** RunRecorder 交付账本 + watcher 三级分级（core/standard/optional）+ emit/beat 分离
- **M2** 三边界收口（file IO encoding sweep / LLMErrorKind 分类 / artifact write sha256 verify）+ `env -i` smoke 复跑 5/14
- **M3** 产品级巡检 `health/{alerts,product_delivery,self_heal}.py` + HUD 业务状态优先 + `self-heal --dry-run` 6 步序列
- 4 条产品红线全兑现（不频繁报错 / 不要手动重启 / 核心缺失才告知 / 重启按钮万金油）
- pytest 1056 passed

### 2026-05-13~14 — 5/13-14 unstaged 工作收尾
- **keyboard_chunk watcher** 接入：Quartz CGEventTap 原生 input source + silence-based chunking
- **OCR watcher 下线**（注释保留可回退）：日均 9 条 / 权重 0.5 / 屏幕录制权限门槛高
- **ContentShape 抽象**（5/14）：tabular_rows / kv_json_blob / document_file + markdown_vault discoverer
- W19 周报多模型对照（豆包 / 文心 / DeepSeek）+ prompt 微调

### 2026-05-09~13 — Daily v3 重构 + Weekly v3
- Daily 渲染器从 6 个收口到 1 个单一入口（`refactor(daily) B/C/D` 三连）
- Daily v3 M5 双层渲染 + 主线锚定层
- Daily 引入 importance_score / value_density 信号，单事件高价值不再被 cluster_size 当噪音
- Weekly v3 full pipeline M0-M3（onboarding + validator + quality + holiday + exec default）

### 2026-05-05~07 — 弹性 + Flagship 策略层
- Gateway 接电熔断器 + 6 个新 capability + watcher 心跳补全
- Daily 引入 flagship/budget 策略层 + 旗舰观察员 prompt
- HUD 重做：入口型卡片，不再 surface 工程指标
- 状态机收口到 state repo 单一权威 + HUD 异常入口闭环

### 2026-05-02~04 — 配置链路 + HUD WebKit
- 顶层 `keypulse setup` 入口 + daemon 检测未配置后端
- HUD 改 WKWebView 渲染 + 视觉对齐 v1 设计稿
- 设置上手文档 `docs/setup-onboarding.md`

### 2026-04-27~30 — Plan A 主体（S0-S5a）
- **S0+S1** 数据源接入器框架（DataSource 接口 + registry + git_log / claude_code / codex_cli / chrome_history / safari_history / zsh_history 等首批 plugin）
- **S1.5** 金矿矩阵 + LevelDB/JSON/markdown_vault discoverers + 字段启发式
- **S2** 跨源关联 v1（entity_extractor + thing_clusterer + thing_renderer + `keypulse pipeline things` CLI）
- **S2.5** 噪声清洗 L0-L4（path_filter / file_whitelist / content_quality / dedup）
- **S2.9** 升维到 LLM Outline 聚类，解决 S2 揭示的 94% 单事件 thing 问题
- **S3a** KnowledgeC.db plugin（macOS 系统级活动）
- **S4a/S4b** 候选金矿半自动确认 CLI + 通用 SQLite Row Reader
- **S5a** 微信红区探测占位 plugin
- **P0-2** privacy_tier 出口拦截（read_all 在 sink 边界过滤红区）

## 关键决策记录

| 日期 | 决策 | 出处 |
|---|---|---|
| 2026-04-28 | 屏幕路线降级，本地数据库直读为主路 | `docs/data-source-architecture.md` §1 |
| 2026-04-28 | 跨源关联是 v1 算法核心（v0 动机分类天花板） | `docs/data-source-architecture.md` §1.2 |
| 2026-04-28 | 接入器框架抽象（DataSource 接口），禁硬编码用户名/路径 | `docs/data-source-architecture.md` §1.3 |
| 2026-04-28 | discover 扫 schema 不读 row，read 由 adapter 主动 query | `docs/data-source-architecture.md` §1.4 L2 |
| 2026-05-14 | OCR watcher 下线（注释保留可回退） | `keypulse/capture/manager.py` 注释 |
| 2026-05-14 | beat（线程心跳）与 emit（真事件）必须字段分离 | `keypulse/capture/base.py` `_last_emit_at_mono` vs `_last_beat_at_mono` 注释 |
| 2026-05-15 | HUD 顶状态走业务交付状态，capability 降级到二级 hint | M3 `hud/summary.py` |
| 2026-05-15 | 通用方案禁止按 app 排序，先想"装新 app 零代码"再设计 | 用户产品原则 |
| 2026-05-15 | py2app + macOS 26 任何 bootstrap 错误都会经 GUI 弹框路径变成静默 abort，preflight 必须实跑 inner --help 兜底 | `scripts/preflight_app.py` `smoke_test_inner_binary` |

## 维护规则

1. **追加，不重写**：milestone 完成后在「已完成」最上面新增一节，不动旧条目
2. **真链路证据**：每个 milestone 行附"真链路实测"（数字 / 路径 / 截图）而不是"已实现"四个字
3. **决策另立**：跨 milestone 的产品/架构决策放「关键决策记录」表，不要散在时间线里
4. **链接代替复述**：详细背景链 `docs/<file>.md` 或 commit hash，不在本文档展开
