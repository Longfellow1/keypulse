# KeyPulse 周报 v3 设计（CPU 版主推）

> 决策已拍板，不再讨论方案，按本文执行。

---

## 1. 背景与拐点

**W19 真实跑了一次**：从 `partial(10 validator fail)` → `ok(0 fail)` 后，发现内容仍走兜底文案——`sanitize_weekly_outputs` 把 LLM 真实产出清空。这暴露了机制错误：**validator fail → 清空 → 兜底**，跟 migration_package 的"失败不清空、用真实数据填槽"哲学反着来。

**黄金标准**（已落档，作为 eval 真值）：
- `/Users/Harland/Go/keypulse/docs/golden-weekly/2026-W19-plain.md`（自用版）
- `/Users/Harland/Go/keypulse/docs/golden-weekly/2026-W19-exec.md`（CPU 领导版）

**用户决策（不再讨论）**：
- **CPU 版（`--style=exec`）作为默认/主推**，plain 降级为自用全文摘录
- validator 思路不整体错，但要升级——不再砍字数/死模板/blacklist 词
- **不上 LLM-as-judge**（成本+延迟+复杂度都不值）
- 参考 `/Users/Harland/Go/data_analysis/migration_package` 的 3 层分工：**规则管结构与数据，LLM 管叙述，失败用真实数据填槽**
- 客观性匹配用**模糊证据**，宁错放也不误杀
- onboarding profile 前置到首次启动 CLI（后期没人愿意回来配）
- 节假日按 4 类抽象 + 通用兜底（伊斯兰/印度教/低活动周都要 cover）
- 周报底部从 `L4=llm L5=cache · 6 次调用` 升为质量分 `92/100 [详情]`
- M0 预留 `rollup_orchestrator(period)` 抽象层 + 周报 metadata schema 含 `user_annotation` 字段（避免后期改架构）

---

## 2. Validator 5 类断言（替换现行规则）

### 2.1 结构断言（exec 必有段）

| # | 段名 | 顺序 |
|---|---|---|
| 1 | TL;DR | 1 |
| 2 | 关键数据 | 2 |
| 3 | 本周关键进展（≥3 主题段） | 3 |
| 4 | 本周风险（表格） | 4 |
| 5 | 没接住的球 | 5 |
| 6 | 下周锚点 | 6 |
| 7 | 生成信息（含质量分） | 7 |

plain 版必有段：主线 / 回声（含没接住的球+一个观察+我的批注块）

### 2.2 覆盖断言（每段必有什么）

- 每个主线段：≥2 个 `[[YYYY-MM-DD]]` 锚点 + ≥1 条决策 + ≥1 条可见产出
- exec 主线段：必有"→ 这意味着"行
- "没接住的球"：≥1 条 + 日期锚点 + 模糊匹配"没看到/没动/没继续/未跟进"语义结尾
- "一个观察"：以 `?` 或 `？` 结尾 + 含跨周/跨主题视角（"上周/连续/N 天/几次"任一）

### 2.3 质感断言（黄金标准独有特征）

- 主线段必含**判断句**——"是定调/不是定位问题/这是 X/真正的/唯一/算/不算/属于"等评判性短语
- exec 主线段叙事 ≥150 字；plain 不限上限
- 每段叙事必含**事实+判断**组合（事实句先于判断句）

### 2.4 反模式断言（黄金里绝不出现）

黑名单（出现即 fail）：
- "沉淀到可复用文档或检查项"
- "暂无可确认的XX" / "本周X有连续记录"
- "已形成可复用的周报素材"
- 积极性兜底空话："虽然遇到困难但仍然推进了"
- 管家腔：卓有成效 / 如火如荼 / 紧锣密鼓 / 齐头并进 / 赋能 / 复盘

### 2.5 客观性断言（反幻觉，模糊匹配）

5 级宽容度，从严到松：

| 级 | 实现 | 例子 |
|---|---|---|
| 1 精确子串 | 数字必须在 corpus 出现 | "857 项测试" → 找到 ✓ |
| 2 同义实体 | 工具/应用建同义簇：Claude ≈ Claude.app ≈ claude(command) | LLM 写"用 Claude" → events 里 command="claude" → 通过 |
| 3 词根匹配 | "重启 daemon" → corpus 含 "重启" 或 "daemon" 任一即过 | 中文分词 |
| 4 数字宽容 | "约 3 次/几颗雷" → 数字"3"找到即可 | "约/大概/几" 不强制对齐 |
| 5 找不到 → flag 而非 fail | 标 "⚠️ 待核" 让 LLM 自己 retry | 修不了再降级 |

### 2.6 句法分类（避免误杀判断句）

| 句子类型 | 客观性查 | 质感查 |
|---|---|---|
| **事实句**（含数字/日期/工具名/主题名） | ✓ 严查溯源 | - |
| **判断句**（"是/不是/真正/这是/属于" + 抽象名词） | ✗ 不查 | ✓ 查主语真实性 |
| **过渡句** | - | - |

L5 prompt 写明："每段先讲事实，再下判断；判断要有立场不要中性。"

### 2.7 行为变更：**不再 sanitize 清空**

- validator fail = retry feedback 喂回 LLM 改一次
- retry 失败 = 输出原 LLM 内容 + 标 `quality: validator_failed` 元数据
- 永不清空 LLM 真实产出走空字符串
- DegradedContentGenerator（仅在 LLM 完全失败=None 时启用）：从 daily-summary 的 cluster.narrative_one_line + 抽出的 [DECISION:]/[SHIPPED:] 标签拼装有内容的退化版

---

## 3. 节假日策略

### 3.1 4 类节日抽象

| 类型 | 特征 | 例子 | 周报策略 |
|---|---|---|---|
| **长假**（≥3 天） | 全地区停工 | 春节/国庆/圣诞/Thanksgiving/Eid/Diwali | 默认假期模板；高活动 → "假期里你还在 X" 特殊叙述 |
| **单日法定** | 1 天停工 | 元旦/劳动节/清明/Good Friday/Memorial Day | 当日补一句；不影响整周 |
| **文化节日** | 不停工但有家庭/社交 | 七夕/Valentine's/Halloween/Mother's Day | 不影响主线；日报标注 |
| **宗教节日** | 区域差异 | Eid/Diwali/Hanukkah/Lent | 按 onboarding 区域+宗教激活 |

### 3.2 通用兜底（不知道节日也能识别低活动周）

| 信号 | 判据 | 兜底动作 |
|---|---|---|
| 突然低活动 | 事件量 < 历史均值 30% 持续 ≥3 天 | 走"低活动周"模板，不强求识别哪个假期 |
| 周期性低活动 | 历史同周也低活动 | 标"疑似周期性假期"，提示用户标注 |
| 用户标注 | `keypulse mark-holiday W19 --reason="家庭婚礼"` | 走假期模板，原因写进 TL;DR |

### 3.3 假期 ≠ 不工作（4 种组合）

每天打两个标签：是否假期日 / 是否高活动。整周看 7 天的组合占比 → 决定模板：

```
工作日 + 高活动     → 标准段
假期 + 高活动      → "假期里你还在 X"段（特殊语义）
假期 + 低活动      → 假期休息段
工作日 + 低活动    → "节奏放缓"段 + 问 dropped_balls 累积
```

### 3.4 数据格式

```yaml
holidays:
  - { name: 春节, region: CN, span: 7, type: long_holiday, date: lunar:2026-01-01 }
  - { name: Christmas, region: [US, EU, AU], span: 3, type: long_holiday, date: 2026-12-25 }
  - { name: Eid al-Fitr, religion: Islamic, span: 3, type: long_holiday, date: lunar:islamic:1-shawwal }
  - { name: Diwali, region: IN, span: 5, type: long_holiday }
fallback:
  low_activity_threshold: 0.3
  user_marked_path: ~/.keypulse/marked-holidays.json
```

第一版：手写中/美/欧 + 主流伊斯兰/印度教节日 50-80 条；第二版接 nager.date 等公开 API。

---

## 4. Onboarding（首次启动 CLI）

`keypulse setup` 必问 5 项 + 1 可选：

| # | 问题 | 选项 | 用途 |
|---|---|---|---|
| 1 | 工作类型 | 开发 / 产品 / 设计 / 写作 / 运营 / 创业者(混合) / 通用 | 决定 profile + 产出指标 |
| 2 | 工作区域 | 中国大陆 / 北美 / 欧洲 / 日韩 / 东南亚 / 多区 | 决定假期表 |
| 3 | 工作节奏 | 标准(周一-周五) / 弹性 / 创业者(7×16) | 假期生效逻辑 |
| 4 | 周报触发 | 周五下午 / 周日晚上 / 周一早上 / 关闭 | 触发时机 |
| 5 | 周报视角 | 自用 / 汇报版 / 两者都要 | 默认 style |
| 6（可选） | 宗教/文化 | 穆斯林 / 印度教 / 犹太教 / 东正教 / 不指定 | 激活对应节日 |

每条都给推荐默认值，跳过即接受。

存档：

```toml
# ~/.keypulse/profile.toml
[user]
work_type = "developer"
region = "CN"
work_mode = "flexible"
weekly_trigger = "fri-pm"
weekly_style = "exec"
religion = ""
created_at = "2026-05-09T10:00:00Z"

[holidays]
follow_strict = false
```

老用户首次升级触发一次配置流程，配过的不再问。

---

## 5. 通用 5 维数据段（exec 关键数据段升级）

### 5.1 通用 5 维（不分职业，每个人都有）

| 维度 | 定义 | 怎么测 |
|---|---|---|
| **决策** | 拍板了几次 | narrative 里 `[DECISION:]` 标签 + "决定/选择/拍板/暂停"语义抽取 |
| **推进** | 连续多日围绕几条主线 | topic_status_snapshot 跨日同主题计数 |
| **新启动** | 本周第一次出现的主题 | 跟上周 snapshot diff，state="started" 且无历史 |
| **协作** | 与人/AI 协作密度 | 识别 Claude/Codex/ChatGPT/Slack/微信/邮件应用 + 时长 |
| **产出** | 留下的可见动作 | 见 5.2 按 profile 适配 |

### 5.2 产出维度按 profile 适配

| Profile | 产出指标（自动统计） |
|---|---|
| 开发 | commit / 测试用例 / 代码行 / PR / issue |
| 产品 | 决策评审 / 文档保存 / 原型迭代 / 评审会 |
| 设计 | 设计稿版本 / 编辑会话时长 / 评审反馈轮次 |
| 写作 | 字数（clipboard 增量推断）/ 发布 / 草稿 |
| 运营 | 数据看板次数 / 邮件发送 / SOP 文档更新 |
| 通用 | 仅 5 维通用 + "本周保存的文件数" |

### 5.3 MVP 顺序

- M2：通用 5 维 + 开发 profile（KeyPulse 自身用户基本是开发，最大群体）
- M3+：产品/设计/写作/运营 profile 按用户量级排队

---

## 6. 加深项

### 6.1 Eval 闭环（替代 LLM-as-judge）

每次 weekly run 后自动跑质量报告：

```
本周报告质量 92/100
├─ 结构完整性    100/100  ✓ 7 段都在
├─ 内容覆盖度    95/100   ⚠ 主线段 3 缺"可见产出"
├─ 文本质感      85/100   ⚠ 缺判断句 2 处
├─ 客观性溯源    95/100   ✓ 数字/日期/工具名全可追溯
└─ 反模式检查    100/100  ✓ 无空话兜底文案

历史趋势：W17=78 → W18=86 → W19=92  ↗
```

周报底部行升级：从 `L4=llm L5=cache · 6 次调用 · $0.18` 改为 `质量 92/100 · 6 次模型调用 · $0.18 [详情]`。质量分落库形成时间线。

### 6.2 沉淀回路（"我的批注 → 长期记忆"）

```
本周周报 → 用户写批注 → weekly-notes.json
                            ↓
下周 L5/L6 prompt 拿到上周批注作参照
                            ↓
                    "上周你说 X，本周..."
                            ↓
                  长期累积 = 个人决策日志
```

实现：
- 周报 markdown 同时存 metadata JSON 含 `user_annotation` 字段
- 用户编辑 markdown 后扫读 → 抽 `> [!note] 我的批注` 块 → 同步 metadata
- 下周 L5 prompt 多一个 `previous_week_user_annotation` 上下文
- L6 探索者用上周批注作为 dropped_balls 候选源

M0 schema 预留 `user_annotation` 字段；M2 后期/M3 落实抽取逻辑。

### 6.3 失败叙事规则

3 条规则纳入 validator 反模式：

1. 失败必须有事实锚点（数字/日期/主题），不能空说"做得不够好"
2. 失败放主线段叙事内（不单独成段），降低视觉权重
3. **禁止积极性兜底**："虽然遇到困难但仍然推进了"——这是空话

### 6.4 Rollup 抽象（月报/季报扩展）

```
rollup_orchestrator(period in {week, month, quarter, year})
  ├─ 时间窗口
  ├─ 跨期状态迁移 diff
  ├─ 关键决策抽取
  ├─ 主题 rollup（多周合并）
  └─ 渲染模板（period-aware）
```

`weekly_orchestrator` 重命名为 `rollup_orchestrator`，period=week 时即当前行为；扩展 month/quarter 只加合并函数 + 模板。M0 改 API + 类型，**不实现** monthly。

---

## 7. 实施路径

### M0（基础设施，1-2 天）

- `keypulse setup` 命令：onboarding 6 个问题 + 写 `~/.keypulse/profile.toml`
- 假期表：手写中/美/欧 + 伊斯兰/印度教主流节日 50-80 条静态文件
- `rollup_orchestrator(period)` 抽象层（API 改名+类型，不动 weekly 实现）
- 周报 metadata schema 加 `user_annotation` 字段（先空槽位）
- 通用兜底：低活动周识别 + `keypulse mark-holiday` 命令

**验收**：
- `keypulse setup` 跑通，profile.toml 落地
- 2026 假期表覆盖中/美/欧 + 主流宗教节日
- W19 现有逻辑无回归（pytest 全过）

### M1（validator 升级 + 不再清空，1-2 天）

- 5 类断言 validator（结构/覆盖/质感/反模式/客观性带模糊匹配）
- 客观性同义簇 + 词根分词 + 5 级宽容度
- 删 `_sanitize_weekly_outputs` 清空逻辑
- 升级 `_fallback_l5_topic` → DegradedContentGenerator 模式
- 周报底部"质量分"显示

**验收**：
- W19 黄金标准过新 validator → 全过
- 当前生产空骨架版（"本周X有连续记录"）→ 全失败
- W19 真实数据重跑：质量分 ≥80
- pytest 全过

### M2（假期生效 + 通用 5 维 + 失败叙事，1-2 天）

- 假期策略：4 种组合 → 4 种模板
- exec "关键数据段"通用 5 维 + 开发 profile 产出
- 失败叙事规则纳入反模式
- 句法分类放行判断句

**验收**：
- 用 2026 春节周（W6/W7）历史数据跑 → 走假期模板
- 用 W19 跑 → 通用 5 维数字正确
- 失败叙事不被反模式误杀

### M3（默认 + 修 L4 + 推广，1 天）

- exec 改为默认 style（无 `--style` 参数即 exec）
- plain 留 `--style=plain` 自用
- L4 reconcile 多策略 JSON 解析（修 degraded）
- L6 cache key 收敛 + 渲染读取（修"没接住的球"渲染丢失）

**验收**：
- 周五自动触发跑出 exec 版
- L4=llm（不 degraded）
- 没接住的球段非空（cache 命中也对）
- 邀请一个非自己的人读一份给反馈

### M4（沉淀回路 + 月报，时机未定）

- 抽 `> [!note] 我的批注` 块 → metadata
- L5/L6 prompt 加 `previous_week_user_annotation`
- monthly rollup 实现

---

## 8. 关键文件位置

| 文件 | 作用 |
|---|---|
| `keypulse/pipeline/weekly_validator.py` | 改成 5 类断言 |
| `keypulse/pipeline/weekly_orchestrator.py` | 删 sanitize 清空、升级 fallback、加质量分、改名 rollup |
| `keypulse/pipeline/daily_summary.py` | 已有 `build_topic_status_snapshot_from_narrative` |
| `keypulse/cli.py` | 加 `setup` / `mark-holiday` / 默认 style 改 exec |
| **新建** `keypulse/pipeline/onboarding.py` | profile 配置 |
| **新建** `keypulse/pipeline/holiday_strategy.py` | 假期策略 + 4 种组合判断 |
| **新建** `keypulse/pipeline/quality_score.py` | 质量分计算 |
| **新建** `keypulse/data/holidays.yaml` | 静态假期表 |
| `~/.keypulse/profile.toml` | 用户配置 |
| `~/.keypulse/marked-holidays.json` | 用户手动标注假期 |
| `~/.keypulse/weekly-cache/<week>/` | LLM cache |
| `~/.keypulse/weekly-quality.jsonl` | 质量分时间线 |
| `docs/golden-weekly/2026-W19-{plain,exec}.md` | 黄金标准 |
| `docs/weekly-report-v3-design.md` | 本文件 |

---

## 9. 已拍板（不再问的事）

1. CPU 版（exec）作为默认/主推
2. validator 留着，思路不整体错，但要升级到 5 类断言
3. 不上 LLM-as-judge
4. 模糊证据匹配，不要死板误杀
5. onboarding 前置到首次 CLI
6. 节假日 4 类抽象 + 通用兜底
7. 周报底部从 LLM 调用次数换成质量分
8. M0 预留 rollup(period) + metadata schema
9. 失败不清空 LLM 产出，只 retry feedback
10. 顺序：M0 → M1 → M2 → M3，不可乱
