# KeyPulse 日报叙事 Pipeline 设计

> 范围：raw events → 证据链 → 两遍 LLM → 今日故事
> 产品范式：骨架优先 + 实体填充（同构 CorpusFlow）
> 人设原则：宁可沉默不要说错话

---

## 1. 问题定位

今天（2026-04-23）的报告暴露了信息流的生肉问题：LLM 收到的是 raw events（文件路径、命令原文、窗口标题）而非线索，所以它只能复读素材。根因不是 prompt，是**算法层缺失** —— 现在只有"采集 → LLM"两层，中间缺了**证据构造层**。

---

## 2. 范式：骨架优先

```
  证据链（算法）        ← 算法管结构，保质量
       │
       ▼  锁定的时地人事 4-tuple
  故事线（LLM Pass 1）   ← 一 unit 一句，幻觉半径 = 0
       │
       ▼
  今日故事（LLM Pass 2）  ← 在故事图上找规律，校准 Pass 1
       │
       ▼
  质量门控（算法）        ← 禁词 / 事实一致性 / 长度
```

**算法管"写什么"，LLM 管"怎么说"**。每一步的中间产物都是 JSON、可审计、可 Golden Set 回归。

---

## 3. 证据链 Schema：时-地-人-事

每个 `evidence_unit` 严格四字段，LLM 只看它，不碰 raw events：

```json
{
  "unit_id": "eu_023",
  "when":  { "start":"09:52", "end":"11:49", "duration_min":117,
             "rhythm":"deep_focus" },
  "where": { "surface":"Safari",              // 表层容器，仅作证据
             "project":"AI运营助手",           // 真实"地"，叙事用这个
             "doc_paths":["/Assistant/..."],
             "url_hosts":[] },
  "who":   { "actor":"user",
             "role":"product/doc-review",     // 冷启动期为 null
             "interlocutors":[] },
  "what":  { "verbs":[{"verb":"reading","weight":0.6},
                      {"verb":"editing","weight":0.3}],
             "objects":["AI运营助手技术开发文档","数据处理方案评审"],
             "scale":{ "chars_typed":320, "clipboard":12, "edits":3 },
             "outputs":[],
             "loose_ends":["数据处理调优.md 打开未编辑"] },
  "confidence": { "where":0.9, "who":0.0, "what":0.7 },
  "raw_refs": ["evt_1523","evt_1524"]
}
```

**关键约束**：
- `where.surface`（Safari）与 `where.project`（AI 运营助手）分离，叙事禁用 surface
- `who.role` 首版从冷启动画像读；画像没有就为 null，LLM 不立人设
- 每字段带 `confidence`，允许 LLM 遇到低置信度时沉默

---

## 4. 层级

```
L0  raw events                 ~558 条    纯噪音 + 素材
  ↓ algo: 过滤 / 去重 / 分型
L1  clean events               ~120 条    清洁素材
  ↓ algo: 时地人事聚合 + 证据增强（§6）★
L2  evidence_units             ~20 条     证据链
  ↓ LLM Pass 1: 单 unit 一句
L3  mini_stories               ~20 条     局部故事线
  ↓ algo: 连接分析（承接 / 转折 / 共现）
L4  story_graph                                   带关系的故事图
  ↓ LLM Pass 2: 全图 + 画像
L5  daily_narrative            主线 / 支线 / 明天线索
  ↓ algo: 禁词闸门 / 事实一致性
L6  report.md
```

---

## 5. 两遍 LLM 的分工与误差控制

**Pass 1（微观）**：一 unit 一调用
- 职责：把时地人事 4-tuple 翻译成一句人话
- 输入窄 → 幻觉半径小
- 禁止跨 unit 说话（不能说"和前面那段一起"）

**Pass 2（宏观）**：一次全局调用
- 输入：Pass 1 的所有 mini stories + 冷启动画像 + 故事图的边
- 职责：找主线、找承接、写明天线索
- **校准权力**：首版保守 —— 只标记 Pass 1 的错误，不重写

**跨遍一致性校验**：Pass 2 输出的实体集 S2，必须 ⊆ evidence_units 的实体集。Pass 2 不允许无中生有。

**成本**：Pass 1 ~20 次 × <1K token + Pass 2 ~1 次 × <4K = ~24K token/天 ≈ $0.05-0.08/天。

---

## 6. 证据增强（L1 → L2，给 LLM 之前的加强手段）★

证据链的风险有两个：**证据薄**（某时段只剩窗口 focus）和**主题散**（15min 内跨两个无关项目）。L1 → L2 的这一步不是机械聚合，是**主动加强**：

### 6.1 从画像里认亲
- 路径/URL/host 先过冷启动画像的 `profile_projects` 字典做归一
- `/Go/Assistant/*.md` 全归到 `project="AI运营助手"`
- 冷启动期画像未就绪 → fallback 到路径公共前缀 + 目录名
- **消灭 "Safari" 进入"地"字段**

### 6.2 拉外部证据进来
- **今日 git commits**：commit message 是最高质量的"做了什么"证据
- **今日改过的文件**（mtime 在今日）：补上"动手改过 vs 只是看看"
- **日历事件**（如已接入）：开会时段是硬事实，替代"在某个 app 里"
- 解决"没在打字的时段在干什么"的黑洞

### 6.3 算"够不够用"分
每个 unit 看三件事：事件条数、权重之和（semantic_weight）、来源多样性（键盘 + 剪贴板 + 窗口三路全 vs 只有一路）。低于阈值的 unit **不进 LLM**，归入"零散时间"桶，报告里一句话带过。

### 6.4 主题散的拆开
15min 窗内实体如果跨两个不相关集合（比如同时有 AI 运营助手和 Mindbones 的文件）—— 拆成两个 unit，不硬揉。避免 LLM 收到一锅乱炖。

### 6.5 从剪贴板和打字推意图
- 剪贴板内容 → 粘到某文件：动作是"搬运 / 引用"
- typing 大段中文 + 在 markdown 文件：动作是"写文档"
- typing 大段 + 在代码文件：动作是"改代码"
- 只有窗口切换 + 无 typing：动作是"浏览"
- **把"地"和"事"用配对证据互证**，比单看一路可信

### 6.6 字段带"把握度"
每字段独立 confidence。LLM 收到 `what.confidence < 0.5` 的 unit，被允许直接省略或说"这段不太确定在做什么"。对齐笔友人设。

**首版必做**：6.1 + 6.2 + 6.3。
**第二版加**：6.4 + 6.5 + 6.6。

---

## 7. 未闭合线索（明天接着用）

L2 `what.loose_ends` 字段标记当下没收尾的事（打开了但没改的文档、查了一半的资料）。跨日保留规则：
- 默认保留 3 天
- 每天 Pass 2 扫一遍：如果 loose_end 提到的实体今日再次出现 → 延期；没出现 → 降权；3 天没出现 → 清除
- 报告"明天线索"栏只显示 Top 2-3 条，避免啰嗦

---

## 8. 与产品原则对齐

| 产品原则 | 本方案落点 |
|---|---|
| 骨架优先 + 实体填充（CorpusFlow）| §3 证据链 schema + §5 两遍 LLM 分工 |
| 人管结构，LLM 管规模 | L0-L2 / L4 / L6 是算法；L3 / L5 是 LLM |
| 白盒可追溯 | 每层都有 JSON witness + raw_refs 指针 |
| 笔友宁可沉默 | §3 confidence 字段 + §8 闸门 fail-closed |
| 消费已落的 schema | §6.3 用 semantic_weight（v12）；L1 过滤用 user_present（v13）|
| 冷启动画像有用武之地 | §6.1 认亲 + §5 Pass 2 画像注入 |

**拍板决定**：
- 角色（who.role）首版来自画像骨架；冷启动画像未就绪时**短期不立人设**，`role=null`，Pass 1 禁止编造人设。
- Pass 2 校准权力首版**保守**：只标记 Pass 1 错误，不重写句子。Golden Set 稳定后再开激进。
- 未闭合线索 3 天窗口，每日 Pass 2 扫描延期/降权/清除。

---

## 9. 上线路径

```
MVP（~3 天）：L0→L1→L2（仅 6.1/6.3）→ LLM Pass 1 → L6
  相比今天：证据层已成型，报告从"素材复读"升到"每段有动词+宾语+规模"
  预期主观分：3 → 6

v1（~1 周）：加 §6.2 外部证据 + Pass 2 + §8 闸门
  预期：6 → 7.5。有主线、有承接。

v2（~2 周）：加 §6.4/6.5/6.6 + 未闭合线索跨日 + 画像注入
  预期：7.5 → 8.5。笔友开始"记得"。
```

---

## 10. 代码落点

```
keypulse/pipeline/
├── evidence.py       # L1→L2：证据链构造 + §6 增强
├── signals.py        # L2→L3 之前的线索提取辅助
├── narrative_v2.py   # L3 Pass 1 + L5 Pass 2
├── story_graph.py    # L3→L4：连接分析
├── gates.py          # L6 闸门
└── evaluator.py      # Golden Set 评测
```

现有 `narrative.py` 保留为 fallback。feature flag 切换，A/B 对照一周。

---

## 11. 评测

扩展 `M_Q.3` Golden Set 到日报粒度：
- 选 20 个典型"好/差"的日报样本作为金标
- 每次改 pipeline 跑回归
- 人工打分 1-10；目标 > 7.5 且样本间方差 < 1.5
- Badcase 结构化回流（CorpusFlow 同款闭环），作为下次 pipeline 调整输入

---

> 文档状态：v2（PM 审批通过：时地人事 4-tuple、两遍 LLM、保守校准、冷启动期不立人设）
> 下一步：冻结 §3 schema，进 MVP 实施

---

## v3 增量（PM 第三轮反馈后的方案强化）

### v3.1 — T4 全日深度推理：早/晚两档

原 Pass 2 拆为 T1 日间片段更新（已有）+ T4 全日终审（新增）。T4 做早晚两档以覆盖不同作息：

| 档位 | 时间 | 触发条件 | 输入 | 输出 |
|---|---|---|---|---|
| T4 早版 | 19:00 | 今日有活动 | 00:00-19:00 全天证据 | 今日故事 v1（终审）|
| T4 晚版 | 23:00 | 19:00 后有 ≥500 字用户输入 | 19:00-23:00 增量 + 早版叙事 | 今日故事终版（修订/追加）|

**边界规则**：跨午夜的活动归入该时刻所属自然日，不做跨日缝合。

### v3.2 — 证据链升级为知识图谱

§3 schema 的 `where.project` / `what.objects` 实体，升级为**实体图 + 关系边**：

**实体规范化**（节点层）
- 每个文件/URL/项目/人名/工具名 → 标准 ID
- 别名合并：`AI运营助手_技术文档_v2.0` ↔ `AI运营助手_技术文档_v3` 解析"v2→v3"演进，合并同一实体
- 层级挂载：`file ⊂ project ⊂ domain`

**关系边**（边层）—— 时序 5 种 + 静态 3 种

| 大类 | 类型 | 含义 | 算法识别信号 |
|---|---|---|---|
| 时序 | 顺承 | A 做完再做 B | unit_A.end → unit_B.start < 10min，A 的产出/悬念 ⊂ B 的起始 |
| 时序 | 派生 | A 进行中分出 B | 单元内部出现第二组不同实体但时间连续 |
| 时序 | 替代 | 放弃 A 改做 B | unit_A 打开但无编辑（低把握度），紧接 unit_B 同类型新文件有编辑 |
| 时序 | 回归 | 中断后回来 | unit_A → unit_X（不相关）→ unit_A'，间隔 <2h |
| 时序 | 取料 | 从 A 搬到 B | 剪贴板 hash：unit_A 出站方向，unit_B 入站方向 |
| 静态 | 属于 | doc A 属于 project X | 路径前缀/host 匹配 profile_projects |
| 静态 | 引用 | doc A 的内容/路径提到 doc B | doc A 文本或剪贴板出现 doc B 路径 |
| 静态 | 同族 | doc A 和 doc B 兄弟 | 同属一个 project，未检出其他时序关系 |

**静态边的产品价值**：即使今天某两个文件没时序关联，LLM 也知道它们在同一个项目的知识图谱里 —— 这是"内在联系"的知识底座。

### v3.3 — 截图 OCR（主动行为 = 高价值证据）

用户截图是心智标记："这一刻值得留底"。键鼠/剪贴板没抓到的价值信号，截图能补上。

- **触发**：文件系统监听 macOS Screenshots 目录新增 png/jpg（`~/Desktop/` 或 `~/Pictures/Screenshots/`）
- **处理**：立即 Apple Vision 本地 OCR（`providers/vision_native.py` 已实现，默认关闭）
- **存**：OCR 文本 + 截图时间 + 文件名；**不存原图、不存图片指针**
- **脱敏**：OCR 文本走和键盘/剪贴板同一条脱敏流水线
- **权重**：`semantic_weight = 0.9`（仅次于键盘输入）
- **失败降级**：OCR 失败 → 仅记录截图行为 + 时间（行为本身是信号）
- **采集来源**：`source = "ocr_screenshot"`，归为用户主动事件（speaker=user）

**场景覆盖**：PM 截竞品设计图、开发者截 stack trace、设计师截参考图 —— 都是键盘+剪贴板抓不到的高价值心智片段。

### v3.4 — 外部证据从"文件系统 + 浏览器"主骨干

原 §6.2 "拉外部证据"调整优先级 —— git commits 只覆盖开发者，改为跨场景通用证据优先：

| 证据源 | 覆盖场景 | 作用 |
|---|---|---|
| 文件系统 mtime | 100%（所有人都产文件）| 区分"动手改过" vs "只是看了看"，识别今日新增产出 |
| 浏览器 tab title | 90%（几乎都用浏览器调研）| URL host 升级为 page title，证据语义化 |
| 截图 + 下载文件夹 | 80%（跨行业通用）| 见 §v3.3；下载为 opt-in（文件名可能敏感）|

**职业专项**（作为可插拔模块）：
- 开发者 → git log / GitHub PR title
- 设计师 → Figma 最近文件
- 写作者 → Obsidian/Notion 近修改笔记
- 研究员 → PDF reader 历史

职业从画像骨架反推（typing 中英比例、切换频率、文件类型分布）。

### v3.5 — 证据单元数量硬上限

原 §6.4 "主题散就拆"反转：**不主动拆，鼓励 LLM 找联系**。

- 一天证据单元**硬上限 10 个**，理想 5-7 个
- 算法默认合并，仅在"时间间隔 >2h 且实体完全不重叠"时强制拆分
- 跨单元的联系交给 LLM 推理（借助 §v3.2 关系边辅助）

数量锚定理由：5 个是普通人一天核心叙事的自然记忆容量，10 个以上用户无法建立认知。

### v3.6 — 运维健壮性（关机/崩溃/睡眠/跨天）

笔记本不是服务器：随时合盖、断电、死机、跨天未开机。服务与算法都要能优雅承接。

#### a. 错过触发：catch-up 与补跑

| 场景 | launchd 行为 | KeyPulse 对策 |
|---|---|---|
| 关机期间错过 T1（12/13/18/21）| `StartCalendarInterval` 会在开机后补跑最近一次错过的时刻 | 接受，但触发时检查"距离该时刻 >2h"则降级为 smoke 而非完整 T1 |
| 睡眠/合盖期间错过 | 同上，唤醒后补跑 | 同上 |
| 错过 T4（19:00 / 23:00）| 同上补跑 | 补跑时标注 `late_fire=true`，narrative 头部写"本次为迟到补订" |
| 连续多天未开机 | 只补最近一次 | 开机 T2 smoke 时扫 `narratives` 表，对最近 7 日内无 narrative 的日子入"补订队列"，用户可 `keypulse obsidian sync --date YYYY-MM-DD` 逐日补 |
| daemon 崩溃 | `KeepAlive=true` 由 launchd 自动拉起 | T2 smoke 在启动后 60s fire，作为"每次重启都会过一遍"的自检 |
| 用户 `kill -9` | 同上 | 同上 |

#### b. 中途崩溃：数据一致性

| 风险点 | 对策 |
|---|---|
| LLM 调用中途崩溃 → `llm_trigger_log` 只写了 pending | 调用前写 `outcome='pending'`，成功/失败再更新为 `ran:ok`/`ran:fail`；启动时扫 pending 超过 10min 的行，标为 `ran:crashed` 不重试 |
| narrative 写 md 文件中断 → Obsidian 里是半截文件 | `tmp + os.rename` 原子写，同目录内操作保证 POSIX 原子性 |
| SQLite 写到一半断电 | 全库开启 WAL 模式（`PRAGMA journal_mode=WAL`），启动时跑一次 `PRAGMA integrity_check` 失败则告警不自动 drop |
| 证据单元写了一半 | 证据单元入库使用单事务，崩溃要么全入要么全不入；不使用跨事务拼装 |
| 同日多次 T4 → narrative 覆盖 | narrative 带版本号（`v1`/`v2_late`），不覆盖旧文件，Obsidian 展示最新 |

#### c. 算法侧：关机期在证据链里如何表达

**核心原则：机器离线不等于用户无活动，但不让 LLM 脑补。**

- evidence unit 带 `machine_online: bool` 字段
- 机器离线时段（关机/睡眠）显式生成一条占位单元：
  ```
  {time: "13:00-18:30", who: "-", where: "离线", what: "机器离线 5h30m", machine_online: false}
  ```
- Pass 1 LLM prompt 显式要求："离线时段不推理、不补写，只说'这段时间机器不在'"
- Pass 2 总述时允许轻量措辞："下午外出了一段"，但不得编造具体事件

**睡眠检测**：macOS `pmset -g log` 可拿到 sleep/wake 时间戳，daemon 每 5min 采样一次，落 `system_events` 表。

#### d. 时钟与时区异常

| 场景 | 对策 |
|---|---|
| 系统时间被手动改（NTP 同步跳变）| narrative 以事件写入时的 `ts_utc` 为准，不重算；T1 窗口判断用 `now - ts_utc`，容忍 ±15min 漂移 |
| 跨时区出差（时区变化）| 固定以本机当前 timezone 切天，不回溯；跨时区当天报告头部标注"今日跨时区" |
| 时钟倒流（罕见）| 若 `now < last_event.ts`，视作异常，T1/T4 全部跳过并记录 `error:clock_rewind` |

#### e. 资源异常：磁盘/权限/内存

| 场景 | 对策 |
|---|---|
| 磁盘剩余 <500MB | OCR/截图模块停采（最吃空间），保留键盘/剪贴板；HUD 红点告警 |
| 磁盘剩余 <100MB | 停止所有写入，daemon 进入只读模式直到用户清理 |
| 辅助功能/屏幕录制权限被撤回 | 对应 watcher 静默降级，不崩溃，T2 smoke 里上报"能力清单变化" |
| LLM 调用网络超时 / rate limit | 指数退避 3 次，仍失败则 `outcome='ran:fail:net'`，当日不再尝试同 kind 触发 |
| 证据单元数异常（0 个或 >100）| 0 个：跳过 narrative 生成；>100：触发硬上限截断 + 告警日志 |

#### f. 冷启动与长期闲置

| 场景 | 对策 |
|---|---|
| 首次安装即开机 | 累计数据 <72h 不触发任何 T4，只做被动采集 + T2 smoke |
| 清数据后首次启动 | 同上，进入冷启动回暖态 |
| 超过 14 天未使用后再开机 | 不补跑历史 T4，只跑当日；画像状态机可能回退一级（长成期 → 咿呀期）|

#### g. 可观测性（让用户/自己能查）

- 所有降级事件写 `system_events` 表（字段：`kind, severity, note, ts`）
- `keypulse doctor` 命令输出：最近崩溃、最近磁盘告警、最近 T4 迟到、pending 超时数量
- HUD 在健康档位变化时给一次静默提示（不打扰，但可查）

---

## v3 决策记录

- T4 时间点：19:00 + 23:00（两档、不可调、按触发条件决定是否 fire）
- 关系边：时序 5 + 静态 3 = 8 类全接受
- 截图处理：立即 OCR，存文本不存图，semantic_weight=0.9
- 外部证据：文件系统 + 浏览器主骨干，职业专项按画像反推
- 证据单元：硬上限 10，鼓励 LLM 推理联系
- 运维健壮性：launchd catch-up + T2 smoke 双保险、LLM 调用双写日志、离线时段显式标注不脑补、WAL + 原子写

> 文档状态：v3.6（PM 审批通过：T4 双档、知识图谱、截图 OCR、外部证据场景化、单元上限、运维健壮性）
> 下一步：MVP 实施（招 1+2+3 首版落地），v3.2 关系边与 v3.3 截图 OCR 作为 v1 核心增量；健壮性要点随 MVP 同步进代码（WAL、原子写、离线占位、pending 双写）
