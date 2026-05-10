# Refactor: Daily Renderer Unification (6 → 1)

- **分支**: `refactor/daily-renderer-unification`
- **日期**: 2026-05-10
- **触发事故**: phase 2 (commit `adc1866`) 接通双层 schema 后，2026-05-09 daily.md 被 daemon 重写成"6 个 renderer 拼出的怪文件"，是过去 4+ 次同类问题（things → skeleton/narrative → narrative_v2 → phase 2 双层）的累积爆发。
- **目标**: 建立"daily 渲染"单一抽象 (`daily_summary.render_daily_markdown`)，删掉所有历史 renderer，零技术债。

---

## 根因（一句话）

KeyPulse 没有"daily 渲染"这层抽象 —— 6 个 renderer 各自做同一件事，没有统一入口、没有统一段名契约。每次"加新格式"都用"加新文件"的方式，旧的不删，靠 `obsidian/exporter.py` 拼。phase 2 是第 6 次重复这个错误。

归类: System boundary 失败 + 补丁扩散 → 升维删旧，禁止再补丁。

---

## 6 个老 renderer（要删的）

| 文件 | 段名 | 谁 import |
|---|---|---|
| `keypulse/pipeline/things.py` | `## 今日概览` `## 今天的事件卡` | cli.py / obsidian/exporter.py |
| `keypulse/pipeline/narrative.py` | `# 今日做的事` H1 + 主线段 | write.py / decisions.py / model.py / exporter.py / __init__.py |
| `keypulse/pipeline/narrative_v2.py` | narrative 改进版 | write.py / exporter.py |
| `keypulse/pipeline/skeleton.py` | `## 今日主线` | write.py / narrative_v2.py / exporter.py / __init__.py |
| `keypulse/pipeline/write.py` | 拼接成 DailyDraft | __init__.py |
| `keypulse/pipeline/decisions.py` | `render_daily_decisions` | write.py / __init__.py |

写盘元凶：`keypulse/obsidian/exporter.py` import 上面 5 个，拼成 daily.md。

---

## 唯一段名契约（source of truth）

```
## 今日要点
## 今天做的事        ← H3 [[anchor-slug|display]] 双层主题段（phase 2）
## 今天的事件卡       ← Obsidian wikilink list（保留老 renderer 真功能）
## 跨日延续          ← 替换错抄的"跨周差异"
## 今天的卡点        ← 条件显示：topic.anchor_state==blocked 时才输出
## 明日的锚点
```

明确删除：今日概览 / 今日主线 / `# 今日做的事`(H1) / 没接住的球 / 一个观察 / 跨周差异 / 今日涉及的主题。

---

## 执行路线（A → F，3 个 SYNC 节点）

### 阶段 A — 补 events wikilink + 段名契约 (~1.5h)
- 在 `daily_summary.render_daily_markdown` 加 `## 今天的事件卡` 段
  - 数据源: `~/.keypulse/events/{date}/*.md`，按时间倒序
  - 输出: `[[../.keypulse/events/{date}/{slug}|title]]`
- 加 `## 今天的卡点` 段（条件显示：state==blocked）
- 加 `## 跨日延续` 段（替换 `## 跨周差异`）
- 删 `## 没接住的球` `## 一个观察` `## 跨周差异` `## 今日涉及的主题` 段
- 真链路: `keypulse daily run --date 2026-05-09` → diff 新输出 vs 当前 5/9.md 的"今天的事件卡"段，wikilink 不丢
- 🔴 **SYNC 1**：跟用户对齐段名/wikilink

### 阶段 B — 所有写盘路径切单一入口 (~2h)
- `obsidian/exporter.py`：删 things/narrative/narrative_v2/skeleton/write/decisions 所有 import；写 daily.md 改成调 `daily_summary.render_daily_markdown(payload)`
- `cli.py`：删 `_render_daily_fallback_with_things` 函数；删 `pipeline_things` 子命令；fallback 走新入口
- 找到 daemon 13:05 写 daily.md 的真实入口，确认接通新 renderer

### 阶段 C — 删历史代码 (~1h)
```
rm keypulse/pipeline/things.py
rm keypulse/pipeline/narrative.py
rm keypulse/pipeline/narrative_v2.py
rm keypulse/pipeline/skeleton.py
rm keypulse/pipeline/write.py
rm keypulse/pipeline/decisions.py   # 先确认无外部依赖
```
- 清 `keypulse/pipeline/__init__.py` 删 ~10 个旧 renderer 导出
- 清 `keypulse/pipeline/model.py:21` narrative import
- 删测试: `tests/test_pipeline_things.py` `tests/test_things_pipeline_e2e.py` `tests/test_pipeline_write.py` `tests/test_pipeline_model_write.py` 等
- 改断言到新段名: `tests/test_obsidian_incremental.py` / `tests/test_sync_incremental.py` / `tests/test_obsidian_exporter.py` / `tests/test_hud_summary.py`

### 阶段 D — 下游适配 (~1h)
- `hud/summary.py:228` `_DAILY_MAIN_SECTION_PREFIXES` 切到 `("## 今天做的事",)`
- `hud/summary.py:232+` H3 解析逻辑确认能解析 `### [[anchor|name]]` wikilink
- `weekly_orchestrator._read_daily_markdown`：grep 验证不依赖老段名
- 🔴 **SYNC 2**：跟用户对齐再做全量回填

### 阶段 E — 历史回填 (~30min)
```bash
for d in 2026-05-04 2026-05-05 2026-05-06 2026-05-07 2026-05-08 2026-05-09; do
  ~/.keypulse/venv/bin/keypulse daily run --date $d
done
```
- 5/1-5/3 跳过（无 summary）

### 阶段 F — Eval + Ship (~1h)
- daily_validator 跑 5/6 → score ≥ 97（黄金 baseline，不退化）
- daily_validator 跑 5/9 → score ≥ 90
- pytest -q 全过
- HUD parse: 显示新 daily.md 的"今天最新"三条不空
- 重启 daemon (`keypulse stop && keypulse start`)，等 cron 自动触发或手动 trigger，确认走新 renderer
- weekly W19 真链路 `keypulse weekly run --week 2026-W19`，quality ≥ 90 不退化
- commit + PR 标题 `refactor(daily): 6 renderer → 1，单一入口`，body 列出删除文件
- 🔴 **SYNC 3**：给用户做 review

---

## 验收标准

| 指标 | 目标 |
|---|---|
| 5/6 黄金 baseline | daily_validator score ≥ 97（不退化）|
| 5/9 score | ≥ 90 |
| pytest -q | 全过（删过测试后剩余的）|
| events wikilink | 5/6 / 5/9 daily.md 含 `[[../.keypulse/events/...]]` 数量 ≥ 旧版本 - 1 |
| HUD "今天最新" | 显示当天主题 H3 不空 |
| weekly W19 | 真链路跑通，quality ≥ 90 |
| daemon 路径 | 重启 daemon 后自动跑出新段名格式 |
| 历史回填 | 5/4-5/9 daily.md mtime 全部新格式 |

---

## 已踩坑（不重蹈）

1. **不要只验 `keypulse daily run` 命令路径** — 这是 phase 2 事故根因。daemon 路径必须独立验证（重启 daemon + 真触发）。
2. **不要假设 phase 2 双层 renderer 完整** — 它漏写 events wikilink 段，是用户真功能。阶段 A 必须补。
3. **删文件前必须 `git grep` 确认无生产代码 import** — Codex 之前在 phase 2 引入第三方依赖未通报，自己 grep 是必须的。
4. **生产 venv ≠ 仓库代码** — `~/.keypulse/venv/` 跟仓库 link 关系要核：`pip install -e .` 还是 freeze 装的？如果是 freeze，重启前要先 `pip install -e .` 重装。

---

## 协作模式

- **Opus（Harland 的决策）**: 架构 / sync 节点裁定 / 用户沟通
- **Codex MCP (gpt-5.5 系列)**: 阶段 A/B/C/D/E 大块实现，独立 worktree，自己 grep 自己删
- **Sync 节点**: A 完成 / D 完成 / F 完成，三次跟用户对齐

---

## 进度记录

- [ ] 阶段 A — events wikilink + 段名契约
- [ ] SYNC 1
- [ ] 阶段 B — 写盘路径切单一入口
- [ ] 阶段 C — 删历史代码
- [ ] 阶段 D — 下游适配
- [ ] SYNC 2
- [ ] 阶段 E — 历史回填
- [ ] 阶段 F — Eval + Ship
- [ ] SYNC 3 → review + merge
