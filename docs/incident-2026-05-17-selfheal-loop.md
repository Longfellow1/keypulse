# 2026-05-17 现状交接：自愈死循环 + 文本采集断 + 日报回归

> 写于重启电脑前。下次进来从「下一步」段开干即可。

---

## 一、症状

1. **HUD 频繁静默退出** —— 跟着 daemon 一起被 SIGTERM
2. **5/17 日报聚类回归** —— 同一个 AI agent 项目被拆成 5 个零散 topic（5/06 金标是 4 个 topic，正确合并）
3. **Obsidian 5/17 daily 显示的是旧版本** —— 新跑出来的全被 quality_gate 拒（thing_count=0<3）

---

## 二、根因链（单一根因 → 三层连锁）

```
macOS AX 权限被撤
    ↓
ax_text / keyboard_chunk watcher emit = 0
    ↓
capability fact/probe 错位：accessibility_permission.ok=false
但 ax_text_watcher.ok=true, keyboard_chunk_watcher.ok=true
（5/12 fa90da6 fact override 没覆盖到 keyboard_chunk / ax_text）
    ↓
healthcheck 看到 alert `PRODUCT_WATCHER_KEYBOARD_CHUNK` (keyboard 24h 无新数据)
触发 self_heal → SIGTERM daemon
    ↓
daemon 死 → HUD 死（HUD 是 daemon 子进程/IPC 客户端）
launchd 拉起 → 15 分钟后又被 kill → 循环
    ↓
副作用：内存里 raw_events 丢、orchestrator 中断、state 复位
    ↓
日报：725 raw events → token_guard 砍到 40 → 全是 window/clipboard 标题（无文本语义粘合）
       LLM 看 40 个零散标题 → 拆 5 topic（每个 cluster time_range start==end）
       quality_gate 看到 thing_count=0 → 拒写 → 旧文件留着误导
```

---

## 三、关键证据

| 项 | 数值 | 文件 |
|---|---|---|
| `daemon starting` 次数 | **498** | `~/.keypulse/keypulse.log` |
| `killed stale daemon` 次数 | **218** | `~/.keypulse/healthcheck.err` |
| 5/17 14:17–15:18 一小时内重启 | **8 次** | 同上 |
| `degraded_streak` | **143** | `~/.keypulse/health.json` |
| `self_heal_triggered` | **true** | 同上 |
| `accessibility_permission.ok` | **false** `(ax_denied)` | `~/.keypulse/healthcheck.log` |
| `ax_text_watcher.ok` | **true**（**应为 false**）| 同上 |
| 5/17 watcher emit_24h | ax_text=0 / keyboard_chunk=0 / ocr=0 / clipboard=20 / window=138 / idle=129 | health.json `product_status` |
| 5/17 daily events_capped | **725 → 40 (token_guard)** | keypulse.log |
| 5/17 daily quality_gate | **REFUSED thing_count=0<3** | keypulse.log |
| 5/06 → 5/17 增量 daily 改动 | `560a1b2` H3 多 cluster 匹配 / `1aa1d4f` v3 收口 / `1d12f1a` wiring-fix / `65dd664` M0-M3 自愈 | git log |

---

## 四、为什么"现行自愈改不如不改"

自愈判断层已经坏了，动作层（SIGTERM daemon）把"1 类信号哑了"放大成"整个采集链路 + HUD + pipeline 中断"。

具体 5 条：

1. **错把上游事实当进程状态来治**：keyboard_chunk 0 emit 是权限被撤的事实，重启进程不会让 macOS 把权限还回来
2. **判断层和动作层信号源不一致**：accessibility=denied 但 watcher.ok=true，自愈基于错误状态判断
3. **重启副作用比放着不管贵**：HUD 死 / pipeline 中断 / 内存数据丢 / state 复位
4. **没退避、没上限、没逃生口**：每 15 分钟一次，永远循环
5. **该做的一个没做**：权限引导 / 单点重启 watcher / 降级 capture 不杀 daemon / HUD 红条

> **第一性原理**：采集稳定是日报的锚点。prompt 层可以慢慢优化，但采集断了或被反复打断，再好的 prompt 也无米下锅。

---

## 五、下一步（重启后直接干）

### Step 0：手动恢复 AX 权限（重启第一件事）

系统设置 → 隐私与安全性 → 辅助功能 → 删 **KeyPulse** → 重加 → 启动 daemon

预期验证：
```bash
# 5 分钟后看
cat ~/.keypulse/health.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['product_status']['watcher_emit_counts_24h'])"
# ax_text / keyboard_chunk 应该 > 0
```

如果 ax_text 还是 0，说明 watcher 自身还有问题，再排查。

### Step 1：让 Codex 改 self_heal（用户先定 3 个决策点）

需要用户先答的 3 个问题（重启回来给我）：

**A. self_heal 改造幅度**
- (1) **彻底砍掉**（推荐）：self_heal_triggered → SIGTERM 路径直接删，只留 alert + HUD 红条 + 用户引导。daemon 真死让 launchd 重启就够
- (2) 保留但严格限缩：只在「进程真死」时 kill；watcher silent fail / capability denied 类一律不 kill
- (3) 改成单点重启 watcher

**B. quality_gate 拒了之后怎么处理**
- (1) **写占位 + 标注采集异常**（推荐）：不留旧版本误导
- (2) 保留旧版 + 顶部红条
- (3) 本次不动，先只修 self_heal

**C. capability fact override 修到什么范围**
- (1) **AX denied 时所有依赖 AX 的 watcher 全部 fact=false**（推荐，最小修法）：ax_text / keyboard_chunk / 其他读 AX 的都覆盖
- (2) 重做整个 fact/probe 分层

### Step 2：Codex brief 模板（决策点定了后用）

```
目标：断 self_heal 死循环 + 修 capability fact 错位 + daily quality_gate 兜底
理由：自愈正在主动破坏采集稳定性，5/17 日报回归就是这条链的副作用

上下文：
- 现状证据见 docs/incident-2026-05-17-selfheal-loop.md
- 关键模块：
  - keypulse/pipeline/daily_orchestrator.py
  - keypulse/pipeline/daily_summary.py
  - 自愈逻辑：grep self_heal / degraded_streak / killed stale
  - capability：grep ax_text_watcher / accessibility_permission / fact override
- 5/12 fa90da6 是上一次 fact override 修复，没覆盖到 keyboard_chunk

验收：
1. 模拟 AX denied 跑一次 healthcheck → 不应 SIGTERM daemon
2. capability 状态：accessibility=denied → ax_text/keyboard_chunk fact=false
3. daily 跑 thing_count=0 输入 → 写占位文件而非保留旧版
4. degraded_streak 升到 N 不再触发 kill，改成 HUD alert
5. 跑 pytest tests/ 全绿
```

### Step 3：验证

1. 重启后 8 小时内观察：
   - `wc -l ~/.keypulse/healthcheck.err`（killed stale daemon 不应增长）
   - `grep -c "daemon starting" ~/.keypulse/keypulse.log` 增长应停在 launchd 自然次数
   - `watcher_emit_counts_24h.ax_text > 0`
2. 5/18 daily 自动跑完后：
   - JSON 里 cluster 应能跨时间合并（time_range 不再 start==end）
   - quality_gate 不再 REFUSED
3. 跟 5/06 金标对照：用 `daily_validator` 跑分

---

## 六、不要做的事

- **不要去改聚类代码**：5/06 ↔ 5/17 用的是同一代 v3 框架，clustering.py 在 5/06 之后没动过。聚类回归是输入侧瘦了的副作用，不是聚类 bug
- **不要先改 prompt**：根因在采集层不在生成层
- **不要在 AX 权限恢复前重跑 daily 对照**：输入还是瘦的，跑出来没意义

---

## 七、相关 memory（已存在，不重复）

- `project_watcher_emit_vs_beat` — beat 只刷心跳不算真事件，0513 weekly 失真同根因
- `project_macos_three_permissions` — AX/Screen Recording/Input Monitoring 缺一就 0 emit
- `feedback_evidence_first` — 异常先定根因再修，不上来就重启 daemon
- `project_ocr_watcher_disabled` — OCR 已下线（解释为何 ocr_text=0）

---

## 八、本次发现的新 anti-pattern（值得固化）

**自愈策略的反模式**：当 detection layer（capability fact/probe）不可靠时，action layer（SIGTERM daemon）会把局部故障放大成全局故障。**自愈的前提是 detection 准；detection 不准时，不自愈 > 乱自愈。**

建议落进 memory：`feedback_no_blind_self_heal` —— 自愈动作必须基于稳定可信的 detection 信号，不准就别动手。重启完确认完整改造方向后再写入。
