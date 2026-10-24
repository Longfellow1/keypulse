# 运行时约定（D36）设计稿

> 状态：已拍板（2026-10-24）。评测门槛见 `docs/Penpal_Eval_PRD.md` §3.7（E8，R1–R7）。
> 来源：深度审计（4 月至今 80+ 次相关提交）+ 外部实战经验调研（GitHub issue、Apple 开发者论坛、HN、维护者复盘）。

## 1. 问题
"现在是否在正常记录"由 6 个出口各自判断（doctor / status / install doctor / HUD / MCP capture_health / 健康检查），依据有 4 种（PID 文件、launchd 登记、心跳、最近事件），阈值各不相同（150 s / 300 s / 10 min / 20 min / 不检查）；重启有 5 条路径；进程之间没有版本校验。由此反复出现：误报漏报、活着但卡死、前台假死、升级后新旧代码混跑、自愈循环。

### 1.1 2026-10-24 真机手测暴露的问题 → 由哪条机制解决
| # | 现场问题 | 根因 | 机制 | PR |
|---|---|---|---|---|
| 1 | `install.sh --dev` 后后台服务仍跑 5 天前的旧代码 | 开发版安装自写 plist、只 load 不替换；进程无版本校验 | §2.3 唯一控制入口（安装统一走 `penpal install init` → `restart`）+ §2.4 自我换新 | 2、3 |
| 2 | doctor 全部"正常"，HUD 显示"已停止" | 两者依据不同；doctor 没有存活检查 | §2.1 运行记录 + §2.2 唯一评估函数 + 一致性矩阵（R1） | 1 |
| 3 | HUD"重启"失败 | 自愈路径扫描 PID、按"有新事件"判成功 | §2.3 `restart` = `kickstart -k`，成功 = 新代次有进展 | 2 |
| 4 | HUD 进程在、菜单栏无图标；`hud status` 说在运行 | App 身份是 Python；单实例只看 PID；系统菜单栏开关独立 | §3 原生启动程序 + HUD 运行记录（已创建 / 系统允许显示）+ 引导 | 4 |
| 5 | 菜单栏多出一个开发时子代理用真实环境启动、没关的 HUD | 开发 / 测试没有强制隔离真实家目录 | §2.5 开发隔离守卫 | 1 |
| 6 | 本机 8 个 `mcp serve`，最老的是 5 天前的旧代码 | MCP 进程无版本校验；宿主关会话后子进程可能残留 | §2.4 自我换新 + 父进程退出 / stdin 关闭即退出 | 3 |
| 7 | 终端命令 3 分钟内没进记忆 | 旧进程仍按 10 分钟轮询（#124 未生效） | 由 1 解决；R4 验收"升级后新调度生效" | 3 |
| 8 | `block-site` 后要手动重启才生效 | 后台服务只在启动时读配置 | §2.4 配置指纹纳入身份：配置变化 = 自我换新 | 3 |
| 9 | 日志仍叫 `keypulse.log`、35 MB 不轮转；`/Applications` 里残留旧 KeyPulse.app | 改名迁移不完整；无日志上限 | §4 删除清单 + doctor 报告遗留物并给出清理命令（不自动删用户应用） | 5 |

## 2. 约定（四层）

### 2.1 一份运行记录（证据，不是结论）
- 只由后台服务写；原子替换的文件 `~/.penpal/run/daemon.json`（不放数据库：存储故障时仍能看到"服务活着、存储坏了"）。
- 字段：`generation`（每次启动唯一）、`pid` + 进程启动时间（防 PID 复用）、`version` + 代码指纹、`started_at`、`written_at`；**进度**：各关键循环（落库循环、各采集器、来源调度）的"完成序号 + 最近完成时间"，以及各组件状态（运行 / 空闲 / 放弃 / 缺权限 / 存储错误）。
- 由关键循环自身推进的进度证明"在干活"；单独的定时线程只能证明"写心跳的那段代码还在"，不能作为存活依据（外部复盘：只看心跳导致假健康；把空闲当失活导致一天重启 46 次）。**空闲是健康状态。**
- HUD 写自己的 `~/.penpal/run/hud.json`：generation、代码指纹、主线程最近响应时间、status item 是否已创建、系统是否允许显示（尽力而为）。

### 2.2 一个评估函数（唯一结论来源）
- `penpal.runtime.evaluate(now) -> RuntimeStatus`，在调用方进程里运行，读取运行记录 + launchd 状态 + 已安装代码指纹。
- 结论为封闭集合：`not_installed / not_running / starting / running / hung / outdated / paused`，外加组件问题列表（带严重度与修复命令）。
- 所有阈值只在这里定义；doctor、status、install doctor、HUD、MCP、健康检查全部调用它。import-linter 合约禁止其他模块读取 PID 文件、launchctl 或运行记录。

### 2.3 一个控制入口
- `penpal.runtime.control`：`restart()` = `launchctl kickstart -k`（job 已加载、plist 未变）；plist 改变或 job 已卸载时走 `bootout` → 有界等待 teardown → `bootstrap`（带重试，规避外部报告过的"bootout 后立即 bootstrap 失败、服务从 launchd 消失"）。
- 新增 `penpal restart`。安装 / 升级、HUD 重启按钮、doctor 修复、看门狗全部调用它。
- 看门狗（现有健康检查 job）：`hung` / `outdated` → restart；连续失败阈值 + 冷却期 + 每小时重启预算，耗尽后只告警不再重启（防自愈循环）。成功判据 = 新 generation 的进度在推进，**不是**"出现新事件"。
- 单实例改用 `flock`（进程退出由内核释放）；PID 只作诊断。

### 2.4 代码身份与自我换新
- 每个常驻进程启动时记下代码指纹，每分钟与磁盘上已安装代码比对；不一致时：
  - 后台服务：完成当前落库后以约定退出码退出，launchd（`KeepAlive` + `SuccessfulExit=false`）从稳定路径（Homebrew `opt` 符号链接，#119）拉起新代码。
  - HUD：`execv` 原地换新。
  - MCP `serve`：在**没有进行中请求**的边界 `exec`，保留 stdio 管道，交接已协商的协议状态；若工具目录有变，发 `notifications/tools/list_changed`。各宿主行为不同（Claude Code 不会自动重连 stdio，Cursor 刷新工具列表不可靠），**必须逐个宿主实测**（Claude Code、Codex、Cursor）。
- 数据库结构版本纳入身份：旧代码发现库结构比自己新，按 `outdated` 处理。
- 配置纳入身份：`config.toml` 的指纹变化时，后台服务同样自我换新，配置改动（如 `block-site`）无需用户手动重启；CLI 提示改为"约 1 分钟内生效"。
- `mcp serve` 在 stdin 关闭或父进程退出时立即退出，不留孤儿进程。

### 2.5 开发隔离守卫
- `tests/conftest.py` 全局（session 级、autouse）把 HOME 指向临时目录；任何测试解析出真实家目录下的 Penpal 路径即失败。
- `penpal.utils.paths` 在设置了 `PENPAL_ISOLATED=1` 时拒绝解析到真实家目录；派给 AI worker 的手动探针一律在 `PENPAL_ISOLATED=1 HOME=$(mktemp -d)` 下运行（写入开发章程 §0）。

## 3. HUD
- 数据读取（SQLite、来源探测、组装 JSON/HTML）在后台线程；所有 AppKit / WKWebView 操作回主线程（外部报告：后台线程碰 status item 会崩溃）。界面显示"数据更新于 X 秒前"。
- App 身份：`Penpal.app/Contents/MacOS/Penpal` 是原生启动程序，进程内加载 Python 运行 HUD，系统看到的是 Penpal 而非 Python。`penpal app install` 用本机 clang 编译（装了 Homebrew 的 Mac 必有）；编译不可用时退回脚本并如实提示。
- 菜单栏显示受系统设置控制，不由应用保证（Plover 在 Tahoe 上出现过"进程名对了但图标不显示"）：HUD 区分"已创建"与"系统允许显示"，不可见时引导用户到"系统设置 → 菜单栏"；验收必须从 Finder / Launch Services 启动，不能只跑可执行文件。

## 4. 删除清单（不包装、直接删）
PID 文件作存活依据（含 `keypulse*.pid` 兼容读取）；self-heal 的扫描 PID 杀进程与"5 分钟内必须有事件"判据；无人读取的 `alerts.json`、`capability_status.json`；`HealthFreshnessCapability`；`install.sh` 中从未调用的 `reload_launchd_plist` 与 `--dev` 自写 plist 分支（统一走 `penpal install init`）；`penpal start` 的手动 fork 守护模式；健康检查里已停用的自愈分支与 streak；`keypulse.log` 命名（改为 `penpal.log` 并轮转）。

## 5. 实施顺序（5 个 PR，各自可验证）

开工前置：按 `docs/ENGINEERING.md` 完成一轮代码整洁审查，并先合并审查产生的清理 PR（死代码、根目录杂物、遗留命名），避免在旧杂物上叠新机制。

| PR | 内容 | 对应评测 |
|---|---|---|
| 1 | 运行记录（含进度）+ 评估函数 + 六个出口全部切换 + import-linter 合约 + 删除旧读取逻辑 + 开发隔离守卫 | R1、R5 |
| 2 | 控制入口 + `penpal restart` + 安装路径统一 + 看门狗（预算 / 冷却）+ flock + 删除 self-heal 旧逻辑与手动 fork | R2、R3 |
| 3 | 代码 / 配置 / 结构身份 + 后台服务 / HUD / MCP 自我换新 + MCP 孤儿退出 | R4（含三宿主实测） |
| 4 | HUD 后台取数 + HUD 运行记录 + 原生启动程序 + 菜单栏可见性 | R6、R7 |
| 5 | 日志改名与轮转 + doctor 报告遗留物（旧 App、旧日志、旧 PID 文件）+ 文档 | 回归 |

测试策略：状态机与 launchd 交互用假 `launchctl` 快速测（CI 的 macOS runner 没有登录 GUI 会话，LaunchAgent 不可靠）；真进程场景在临时 HOME 里直接起 `penpal serve`（不经 launchd），用 `SIGSTOP` / `SIGKILL` / 改指纹模拟；R4 三宿主与 R7 菜单栏在真机手测。
