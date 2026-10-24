# Penpal 代码规范（给人和 AI worker）

## 边界
- 核心（`penpal/` 除 `hud/`、`app_bundle.py`）不调用大模型，不引入 UI 依赖；HUD/App 只调用 CLI 同一套核心函数，不写业务逻辑（D33）。
- 新依赖必须说明理由；优先标准库。`lint-imports` 的分层约束不得破坏。
- 支持 Python ≥3.11：不用 3.12+ 语法（f-string 表达式里的反斜杠/同引号嵌套、`type X = ...`、PEP 695 泛型）。

## 隐私与安全（硬规则）
- 仓库公开：代码、测试、fixture、issue、PR 里不得出现真实用户数据。
- 不读真实数据目录：`~/.penpal`、`~/.keypulse`、`~/.claude`、`~/.codex`、`~/.zsh_history`、`~/Library`、浏览器数据、`~/.cursor`。
- 测试与手动探针一律用临时 HOME / `tmp_path`；不 `launchctl`、不启动 GUI、不做系统级安装。
- 令牌明文不落盘、不进日志；新增采集或导入路径必须经过现有的脱敏与域名排除（`accept_event` / `capture_gate`）。

## 写法
- 改动最小化，贴合周边风格：命名、注释密度、错误处理方式与相邻代码一致；不顺手重构无关代码。
- 注释只写“为什么”，不写“做了什么”。
- 错误要具体：抛出/记录能定位问题的信息，不吞异常。
- 用户可见文案：中文为主，README/文档中英两份同步（`README.md`/`README.en.md`、`docs/privacy*.md`）。

## 测试
- 每个行为修改配一个测试；修 bug 时先确认测试在旧代码上失败（mutation check）。
- 提交前：`python -m pytest -q` 全绿（本地可 deselect 已知环境问题 `tests/test_multi_host_mcp.py`，CI 必须全绿）+ `lint-imports`。

## 跨进程约定（D36）
- 运行状态只由 `penpal.runtime` 的评估函数产生；其他模块不得自行读取 pid 文件、launchctl 或运行时状态键。
- 启动 / 停止 / 重启只走 `penpal.runtime` 的控制入口（launchd）。
- 改动跨进程约定（状态记录、权限判定、数据库结构、MCP 协议）时，PR 写明影响的约定与出口，并补 Eval PRD §3.7 的矩阵 / 场景用例。
- 修真实环境报告的问题：先写一条会失败的场景用例，再修。

## Git
- 从最新 `origin/main` 开分支，一个 PR 一件事；不 `git add -A`，不提交 `.codex-rules.md`、`.venv`、日志。
- 提交信息用 conventional 前缀（`fix:`/`feat:`/`docs:`/`chore:`），结尾加 `Co-Authored-By` 行。
- worker 不 push、不合并、不打 tag；由主控审核 diff 与 CI 后合并。
