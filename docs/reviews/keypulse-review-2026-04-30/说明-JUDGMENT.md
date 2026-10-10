# 数据采集策略 · 产品判决（2026-04-30）

> 本文档是 KeyPulse 数据采集策略的固化决策记录。后续任何"加新 watcher"、"重新打开 keyboard tap"之类的提议，必须先回来对照本判决书并升级证据等级。

---

## 决策摘要

**判决：DE-SCOPE / 降级**

KeyPulse 的数据采集策略从 v0（深度 watcher，全开权限）转为 **v1 主路径（无权限）+ v0 兜底（默认关闭，opt-in）**。

| 数据源 | 处置 | 默认状态 | 需权限 |
|--------|------|---------|--------|
| **v1 sources/plugins/\*** | 主路径 | 全开 | 无 |
| clipboard | 留 | on | 无 |
| idle | 留 | on | 无 |
| window | 留 | on | （NSWorkspace 可，AX 否） |
| ax_text | 留作兜底 | **off** | Accessibility |
| OCR | 留作兜底 | **off** | Screen Recording |
| keyboard_chunk | **砍** | — | Input Monitoring（已删） |

**新用户安装路径：0 权限弹窗即可使用。** 进阶兜底功能在 `~/.keypulse/config.toml` 显式打开。

---

## 决策依据

### 病灶诊断（Strike 1）

**替换成本失明 + 万能入口症**

v0 watcher 路径（OCR / AX / keyboard tap）当初的设计逻辑是"既然能拿，就都拿"。但**永远没把 macOS 权限的真实代价算进 ROI**：

- 装机时 4 个权限弹窗 = 冷启动门槛
- 用户看到 "Input Monitoring" 字样就警觉 = 信任损耗
- macOS 静默轮换权限 = 持续维护负担（**2026-04-30 当天发生**）
- launchd 跑 Python 拿不全权限 = 长期 bug 来源

KeyPulse 的卖点是"私人数据感知"，但安装流程长得像 keylogger。两件事天然冲突。

### 假设审讯（Strike 2）— ROI 矩阵

| 数据源 | 需要权限 | v1 替代覆盖 | 边际价值 | ROI |
|-------|---------|------------|---------|-----|
| keyboard_chunk | Input Monitoring | claude jsonl + chrome history + IDE 文件 + zsh history 覆盖 ~80% | 低 | **负** |
| ax_text | Accessibility | 同上 + 浏览器历史 + markdown vault | 中（特定 app 兜底） | **保留兜底** |
| window 焦点 | Accessibility | knowledgec.db 95% 覆盖 | 极低 | （走 NSWorkspace 不需要 AX） |
| OCR | Screen Recording | 无替代，但极低频 | 边际（PDF / 截图兜底） | **保留兜底** |
| clipboard | 无 | 无替代 | 用户主动行为强信号 | 正 |
| idle | 无 | 无替代 | 弱信号但零成本 | 中性 |

**关键认知**：
- keyboard_chunk 完全可替代，砍掉不损失能力
- ax_text + OCR 在 v1 不覆盖的场景（Preview 看 PDF / 私有应用 / 截图内容）是唯一兜底，**保留但默认关闭**
- 砍掉默认权限请求，新用户体验从"4 个弹窗劝退"变成"装上即用"

**未验证的核心假设**（全 L0），今天部分被证伪：

1. ~~用户愿意为更深数据捕获付出 4 个权限弹窗~~ — **2026-04-30 证伪**：macOS 权限静默掉链，daemon 半死半活
2. OCR/AX/keyboard tap 的边际数据对 daily narrative 有显著贡献 — 仍未验证，但已证非必需
3. 用户能容忍信任 narrative 与 "input monitoring" 的冲突 — 没问过

### 失败预演（Strike 3）— 不做这个决策的话

```
Month 1: 你给朋友 demo，他兴致勃勃装上
Month 2: 朋友看到 "Input Monitoring" 弹窗，犹豫 5 秒，关了。
         一周后说"那个工具我没在用"
Month 3: 你的 macOS 升级，权限静默掉（重演今天）
         你忙别的没注意，daily 连续 5 天空白
Month 4: 你想推广 KeyPulse 到一两个深度用户
         发现"我得手把手带他过 4 个 macOS 设置"
Month 6: KeyPulse 停留在"自己用，没法分发"
         因为分发门槛 = 信任 + 冷启动 = 不可承受
```

**Root cause**：v0 路径的边际数据价值，**永远赶不上**用户信任 + 冷启动 + macOS 权限维护这三笔成本。这不是工程问题，是产品定位与采集策略的错配。

---

## 实施路线（Phase 划分）

| Phase | 内容 | 状态 |
|-------|------|------|
| A | 砍 keyboard_chunk（watcher + manager 接线 + config + tests） | 派 Codex |
| B | ax_text + OCR 默认 off（仓库模板，本地不动） | 我自己改 |
| C | README 重写信任 narrative | **用户自己写**（产品 narrative 不该 LLM 代写）|
| D | BaseWatcher 心跳超时检测（补 P0-1 盲点） | 我自己实现 |

---

## 后续 watcher 加入 gate（强制规则）

任何加新 watcher / 重启已删 watcher 的提议，必须满足：

1. **必要性**：v1 sources 已被证明（L2 以上证据）不能覆盖该场景
2. **权限承诺**：明确列出需要的 macOS 权限，并写入 README "可选权限"章节
3. **默认关闭**：除非零权限，否则默认 `false`
4. **失败模式**：实现 `HEARTBEAT_TIMEOUT_SEC` 和明确的失败日志（不能再静默卡住）
5. **回到本判决书 review**：补充新 ROI 表，证据级别 ≥ L2

不满足以上任一条 = 自动 KILL，不进 PRD。

---

## 证据级别与重审条件

- **当前判决**：基于 L2（行为观察 = 2026-04-30 macOS 权限静默失效的实际事件）+ 产品直觉
- **升级到 L3 的方法**：选一个朋友 / 同事，**纯 v1 模式**装一周 KeyPulse，看 daily 质量是否够 70 分
- **重审触发条件**（任一）：
  - L3 实验显示 daily 质量 <70 分，且确认根因是缺 v0 数据
  - 用户多次主动反馈 "我装了 ax_text 之后日报质量提升 X%"（L1 → 升级到 L2 需复现）
  - 出现新的 macOS API 让权限申请门槛降到 1 次零摩擦

**只被新证据说服，不被功能想象、技术好奇心、沉没成本说服。**

---

## 决策签署

- 决策人：Harland（用户）
- 复核：Claude Opus 4.7
- 日期：2026-04-30
- 关联文档：`./README.md`（架构 review）、`../keypulse-review-2026-04-25/`（上次 review）
