# KeyPulse 架构 Review · 2026-04-30

> 主席：Opus（claude-opus-4-7）
> 三路 scout：Haiku（claude-haiku-4-5）
> 范围：从原始事件采集 → 中间清洗处理 → LLM 出参的整条链路
> 结论性质：长期参考资料，决定 Phase 0~3 的修复节奏

---

## 一、Review 范围与方法

### 1.1 Review 的目标

这次 review 不是为了找 bug 列表，而是回答三个 PM 视角的问题：

1. **当前架构能不能稳定跑下去？**（稳健性）
2. **半年后再加一个数据源 / 一个新清洗规则 / 一个新 narrative 模板，要改几个地方？**（扩展性）
3. **当前的"双轨制"过渡期会不会变成永久态？**（架构债）

### 1.2 三层链路

KeyPulse 的核心数据流是：

```
[采集层] sources/* + watchers/*  →  raw_events / SemanticEvent
                                         ↓
[处理层] cleaning/* + things/* + dedup/* + quality_gate
                                         ↓
[LLM 层] narrative_v2 + skeleton + things narrative  →  日报 markdown
```

每一层的 review 由一路独立 Haiku Explore agent 扫描，互相不通气。三路扫完以后由 Opus 做交叉对比、归并共性根因、排优先级。

### 1.3 三个维度

每路 review 都按"架构 / 稳健性 / 扩展性"三个维度展开，避免只盯具体 bug。

- **架构**：模块边界、职责划分、是否有单点修改
- **稳健性**：长跑是否会崩、异常处理是否完整、并发/重启/恢复
- **扩展性**：加新东西的成本（要改几个地方）

### 1.4 方法学说明

- 三路 scout 并行，避免互相污染结论
- 主席只看产出，不预设答案
- 每条问题必须给文件:行号定位，否则不计入清单
- 共性根因从三路结论里横向抽出来，不是从单层 Top 3 直接拼

---

## 二、共性根因（4 条）

三层的具体问题加起来 30+ 条，但归并后只有 4 条共性病根。修这 4 条就解决一大半，单点修补只是按下葫芦浮起瓢。

| 共性根因 | 在采集层 | 在处理层 | 在 LLM 层 |
|---------|---------|---------|----------|
| **散落病**：缺注册中心 | 加 plugin 改 3 处 | cleaning 在 3 处各写一遍 | prompt 在 3 处各写一遍 |
| **静默吞噬病**：失败 = 返回空 | discover/read 异常被吞 | 过滤无原因追踪 | API key 失败/JSON 失败/空响应都吞 |
| **双轨制病**：v0→v1 迁移中间态 | raw_events vs SemanticEvent 脱节 | v0 cleaning vs v1 cleaning | v1/v2/skeleton 三条 narrative |
| **横切失守病**：privacy 没贯穿 | privacy_tier 标了但没人用 | — | metadata_json 全透传 LLM，两套脱敏不对齐 |

### 2.1 散落病（缺注册中心）

**症状**：每加一个新东西（plugin / 清洗规则 / prompt 模板），都要改 3 个不同位置的代码。

**根因**：没有"注册即生效"的统一入口。模块都是平铺的，调用方需要显式列举。

**典型表现**：
- 采集层加新 plugin：要在 plugin 目录建文件 + 在 registry 里 import + 在 manager 里挂 watcher
- 处理层加新清洗规则：要决定放 v0 (`fragments.py`) 还是 v1 (`cleaning/`) 还是 exporter (`_meaningful_item`)，三处都可能漏
- LLM 层加新 narrative 类型：要改 4 个文件（model.py / narrative_v2 / skeleton / 调用方）

**风险**：随着数据源和场景扩展，每加一个新 source 改动半径会越来越大，最终没人敢动。

### 2.2 静默吞噬病（失败 = 返回空）

**症状**：链路里任何一层失败，下游拿到的都是"空"，看不出是真的没数据还是上游崩了。

**根因**：异常被 try/except 包住直接 return None / [] / 空 dict，没有 `FailureReason` 这种结构化的失败语义。

**典型表现**：
- `registry.read_all()` 单个 plugin 抛异常，整段被吞，调用方拿到部分结果但不知道哪些 plugin 失败了
- `fragments.py` 的 L1-L5 拦截规则丢弃事件不写日志，事后排查"为什么这条没出现在日报里"无法定位
- `_resolve_api_key` 取 Keychain 失败返回 None，下游传空 key 拿 401，跟"key 配错了"混在一起

**风险**：质量问题归因极慢，发现"日报变差"以后不知道往哪查。

### 2.3 双轨制病（v0→v1 迁移中间态）

**症状**：每一层都同时存在新旧两套实现，互相覆盖、互相不一致。

**根因**：每次大重构都是"先建新管道，老的留着兜底"，但合并那一步从来没真正发生。

**典型表现**：
- 采集层：`raw_events` 表（v0 给统计用）和 `SemanticEvent` schema（v1 给 narrative 用）字段不对齐
- 处理层：v0 的 `fragments.py` + v1 的 `cleaning/` 注册器 + exporter 的 `_meaningful_item` 三套清洗规则
- LLM 层：things narrative（v1）+ narrative_v2 三 pass + skeleton 动机骨架，三条路径互相不调用

**风险**：双轨制本身不致命，但当"修一处忘改另一处"成为常态时（参见 P1-#8 quality_gate），就会出现互相不一致的隐性 bug。

### 2.4 横切失守病（privacy 没贯穿）

**症状**：privacy_tier 在采集层认真标了，但下游没有任何执行点。

**根因**：privacy 是横切关注点，需要在每层都有执行钩子，但当前只有"标记"，没有"过滤/脱敏"的契约。

**典型表现**：
- `sources/types.py:50` 定义了 privacy_tier，plugin 也按规则赋值（approved_sqlite=yellow, wechat=red）
- 全链路 grep 不到任何根据 privacy_tier 做拦截/脱敏的代码
- LLM 层 `narrative.py:89` 把 metadata_json 全透传，红区数据可能漏到 prompt

**风险**：这是隐私事故的潜在源头。一旦红区数据（微信内容）真的进了 LLM 调用，没法事后追责也没法回滚。

### 2.5 与 cleaning-unify 重构的关系

`refactor/cleaning-unify` 这刀只解决了双轨制病在处理层那一段（v0 cleaning vs v1 cleaning 合并）。它没碰：
- 散落病（清洗规则散布只是双轨制的一部分，注册中心才是根因）
- 静默吞噬病（cleaning-unify 不带过滤原因追踪）
- 横切失守病（privacy 流向）
- 采集层和 LLM 层的双轨制

所以即使 cleaning-unify 顺利合并，剩下三条病根还在。下面的 P0/P1 清单就是按"这刀解决不了的部分"组织的。

---

## 三、问题清单

### 3.1 P0 必修（4 条）

#### P0-#1 watcher 线程崩了不重启

- **位置**：`watchers/base.py:21-32` + `watchers/manager.py:214-220`
- **现象**：`_run` 里抛异常，daemon 线程死掉。`manager` 没有心跳监控也没有重启逻辑。
- **影响**：daemon 长跑必爆。线程死后用户不知情、开始静默丢数据，等到下次重启 daemon 才恢复。
- **为什么 P0**：这条是"看着没事，跑几天必崩"型隐患。已经在生产上用了，不是理论风险。

#### P0-#2 privacy_tier 标了但 nobody 用

- **位置**：`sources/types.py:50` 定义；全链路 grep 无执行点
- **现象**：plugin 按 `red/yellow/green` 标了，但 `registry.read_all()` 和下游 cleaning / narrative / exporter 都不读这个字段
- **影响**：红区数据（微信内容、approved_sqlite 里的私信表）会无差别进入 LLM prompt
- **为什么 P0**：隐私事故的直接路径。一旦发生，是用户信任崩塌级别的问题，不可挽回

#### P0-#3 raw_events INSERT 无幂等

- **位置**：`store/repository.py:15`（裸 INSERT，content_hash 字段已存在但未用作 unique key）
- **现象**：watcher 重启后会从原文件重读，emit 重发，raw_events 表里同一条事件出现多份
- **影响**：下游统计（事件计数、things 聚类）全部错位；dedup 模块拿到重复输入会出现"看起来去重了实际没去重"的假象
- **为什么 P0**：这条会污染所有下游的数据基线，越拖越难修（历史数据需要清洗）

#### P0-#4 LLM 输入未脱敏 metadata_json

- **位置**：`narrative.py:89`（透传 metadata_json 到 prompt）
- **现象**：v1 路径有 `_sanitize_event` 但作用在 aggregate 后；v2 有 `sanitize_unit`；metadata_json 整体没过任何一层脱敏
- **影响**：红区/黄区数据通过 metadata 字段漏到 LLM
- **为什么 P0**：和 P0-#2 是一对（标记缺失 + 执行缺失）。修了 #2 没修 #4 等于没修

### 3.2 P1 应修（5 条）

#### P1-#5 过滤原因追踪

- **位置**：`fragments.py`（L1-L5 静默 drop）
- **现象**：被丢弃的事件无日志、无原因记录。事后排查"为什么这条没出现在日报里"无法定位
- **影响**：质量问题归因极慢；调阈值的时候只能瞎调

#### P1-#6 things outline ID 不稳定

- **位置**：`session_renderer.py:116`（`thing.id = hash(LLM 生成的 title)`）
- **现象**：LLM 输出的 title 略有差异就导致 thing.id 变化，跨日聚类不稳定
- **影响**：things 跨日报合并失效；用户感知"同一件事每天看起来都是新的"

#### P1-#7 API key 失败语义分级

- **位置**：`model.py:242-254` `_resolve_api_key`
- **现象**：Keychain 不可用 / key 不存在 / auth 失败三种情况都返回 None，调用方混在一起当 401 处理
- **影响**：daemon 环境 Keychain 经常不可用（参见 b192a56 的 .env 补丁），用户看到"LLM 没响应"但不知道是哪种失败

#### P1-#8 bootstrap 质量门槛两套不一致

- **位置**：`obsidian/quality_gate.py`（已改，commit 45123a0）vs `pipeline/gates.py`（未改）
- **现象**：bootstrap 期跳过门槛的语义只在 obsidian 路径生效，pipeline 路径还在用旧逻辑
- **影响**：有 baseline 后两路 gate 给出不同决策，日报生成节奏出现不可预期的 flapping

#### P1-#9 plugin read 并发 + 重试

- **位置**：`registry.py:52-93`
- **现象**：`read_all` 串行调用每个 plugin。一个 plugin 卡住整链路停。异常被吞，无法区分临时故障和永久故障
- **影响**：单点卡顿放大成全链路停摆；瞬时故障（sqlite 锁、文件被占用）当成永久故障跳过

### 3.3 P2 可选（6 条）

> P2 是"应该做但不紧急"。列出来不展开，留作 Phase 3 规划输入。

1. SemanticEvent metadata 字段缺乏规范（plugin 各塞各的）
2. SQLite 跨进程并发访问无显式锁/超时（已有 WAL，但 busy_timeout 默认 0）
3. Timezone 处理双轨（UTC 转换路径 vs 原始时间路径）
4. 阈值硬编码 20+ 处（_L1_WHITELIST_LEN, _L2_MIN_ENTROPY, _L4_MAX_AVG_DISTANCE 等）无配置化
5. dedup 与内容质量无互反馈（dedup key 是 `(source, intent, artifact)`，可能把被 L3 过滤的事件和正常事件归同组）
6. Prompt 注入无防护（WorkBlock theme/primary_app 直接拼 prompt，user_intent 来自昨天日报可能被污染）

---

## 四、修复方案设计

### 4.1 Phase 0：P0 紧急止血（本周）

**节奏**：4 条独立 commit，分支 `fix/p0-stability`，Opus 亲手实现。

#### Phase 0 / P0-#1 watcher 自愈

- **定位**：`watchers/base.py` 的 `_run` 方法 + `watchers/manager.py` 的线程启动逻辑
- **改动点**：
  - `Watcher` 基类加 `_supervise` 包装层：捕获 `_run` 所有异常，记录 traceback 到日志，标记状态为 `crashed`，重启计数 +1
  - `manager` 加心跳轮询：每 60s 检查每个 watcher 状态，`crashed` 状态下指数退避重启（30s / 2min / 10min / 1h 上限）
  - 暴露 `manager.health()` 给 `keypulse status` CLI 看
- **验收标准**：
  - 在 watcher `_run` 里 raise 测试异常，daemon 不死，60s 内自动重启
  - 重启 5 次以上的 watcher 进入 `degraded` 状态，状态接口可见
  - 单元测试覆盖：正常崩 / 持续崩 / 偶发崩三种场景

#### Phase 0 / P0-#2 privacy_tier 全链路过滤

- **定位**：`sources/types.py:50` 标记侧；`registry.py` / `cleaning/` / `narrative*` 执行侧
- **改动点**：
  - 在 `registry.read_all()` 出口加一个 `PrivacyFilter` 中间件：根据用户配置的 `privacy_max_tier`（默认 yellow）过滤 red 事件
  - red 事件进入"安全旁路"：只保留 timestamp + source + 占位 placeholder（不带原文 / metadata），用于统计但不进 LLM
  - 配置项加到 `~/.keypulse/config.toml`：`[privacy] max_tier = "yellow"` / `red_action = "drop" | "redact"`
- **验收标准**：
  - 配置 max_tier=yellow，wechat plugin 出来的 red 事件不出现在 narrative prompt 里
  - 配置 max_tier=red，所有事件都进；这个开关用户必须显式打开
  - 测试覆盖：默认配置下 grep 任何 narrative 输出，无 red 事件原文

#### Phase 0 / P0-#3 raw_events 幂等

- **定位**：`store/repository.py:15`
- **改动点**：
  - schema 加 unique index：`CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_events_hash ON raw_events(content_hash)`
  - INSERT 改 `INSERT OR IGNORE`，受影响行数 = 0 时记 `dedup_skipped` 计数（不写 warn 日志，太吵）
  - content_hash 计算放到 plugin 侧（emit 时算好），不要在 repository 侧算（避免重复算）
  - 历史数据：写一个一次性脚本 `scripts/dedup_raw_events.py` 按 content_hash 去重，输出"删了多少行"报告
- **验收标准**：
  - 同一条事件 emit 两次，raw_events 只多一行
  - 历史数据脚本跑完，重复行清零
  - 测试覆盖：watcher 重启场景模拟

#### Phase 0 / P0-#4 metadata_json 脱敏

- **定位**：`narrative.py:89` + 同级 `_sanitize_event` / `sanitize_unit`
- **改动点**：
  - 抽一个统一的 `sanitize_metadata(metadata, privacy_tier)` 函数，所有进 LLM 的事件都过这层
  - red 事件 metadata 直接抹空，只保留 schema 不保留值
  - yellow 事件 metadata 应用字段级 redact 规则（path 只留最后一段、url 去 query、email 去 local-part）
  - 把 `_sanitize_event` 和 `sanitize_unit` 都改成调用这个共享函数
- **验收标准**：
  - 单元测试：构造 red/yellow/green 三种事件，验证脱敏后输出
  - 集成测试：跑一遍 narrative 链路，grep prompt 字符串无敏感字段（密码、token、wechat 原文）

### 4.2 Phase 1：等 cleaning-unify Step 1 真实数据验证（下周）

**节奏**：30 号这次 review 出来后，先合 P0，再用真实数据跑 cleaning-unify Step 1 一周观察。

**验证什么**：
- Step 1 合并后日报质量没有掉（用 4 月份后半月做基线）
- filtered count 没有异常飙升或暴跌
- things 聚类输出和 Step 1 之前对比，差异在可解释范围

**通过标准**：连续 3 天日报对比无质量退化，再合 main。

### 4.3 Phase 2：cleaning-unify Step 2/3（再下周）

**节奏**：Step 1 验证通过后，按 cleaning-unify 设计文档继续 Step 2 / Step 3。这部分不在本次 review 范围内，但要确认：
- Step 2 落地时一并解决 P1-#5（过滤原因追踪），不要拖到 Phase 3
- Step 3 落地时检查 dedup 与 cleaning 反馈（P2 第 5 条）是否能顺手做掉

### 4.4 Phase 3：注册中心化重构（更后）

**这是大工程**，要单独立项。三层各做一份注册中心：

- **采集层**：`PluginRegistry`，plugin 文件丢进目录自动发现，不用改 import 列表
- **处理层**：`CleaningRuleRegistry`，每个 rule 是一个带 metadata 的对象（rule_id / level / threshold / decision_fn），注册即生效
- **LLM 层**：`PromptRegistry`，prompt 模板带版本号、变量 schema、敏感字段列表，所有调用方走统一接口

Phase 3 是解决"散落病"的根本手段。但要等 P0/P1 都收拢、cleaning-unify 全部落地后再启动，否则会和正在做的事冲突。

---

## 五、P1 详细设计

> 这一章原本作为本次 review 的核心交付。**先读 §5.0 再读后续 spec。**

---

### 5.0 P1 优先级再评估（2026-04-30 复盘补记）

P0 落地后回头审 P1，发现以下事实需要承认：

#### 5.0.1 P1 不紧急

P0 那 4 条用户能立刻感受到（隐私事故、丢数据、daemon 崩溃、重复入库）。
P1 这 5 条用户基本感受不到：
- API key 报错有 `.env` fallback 兜底（commit b192a56）
- 过滤无 trace 只在调试日报质量时才痛
- gate 双轨没出过真实 bug
- thing.id 不稳定的"严重度"是基于"如果跨天 join 会出问题"的假设
- plugin read 并发 + 重试是 review 时的过度理论化，没有生产抖动证据

#### 5.0.2 原 P1 详细设计存在估计盲点

| 条目 | 原方案 | 未验证假设 |
|------|--------|----------|
| P1-#5 过滤原因追踪 | FilterDecision dataclass + 新 sqlite 表 | 真实需求是否需要"结构化记录"，还是 logger.debug 一行就够 |
| P1-#6 thing.id 稳定性 | 改 hash 输入 | thing.id 是否真的被跨天消费？没验证 |
| P1-#7 API key 失败语义 | ApiKeyError 三个子类 + boot check | 是否需要异常体系，还是 except 拆三个分支打不同 reason 就够 |
| P1-#8 bootstrap gate 双轨 | 抽 shared 模块 | 两套 gate 是否同一职责？没验证 |
| P1-#9 plugin read 并发 + 重试 | ThreadPoolExecutor + 异常分类 | 真有抖动吗？无证据 |

#### 5.0.3 整合修复 / 大道至简版（推荐执行路线）

**Step 0（30 分钟 spike，验证假设）**
- grep `thing.id` 调用方：是否跨天 join？
- grep `obsidian/quality_gate.py` 与 `pipeline/gates.py` 调用图：同一职责双实现，还是不同职责？

**Step 1（spike 后做减法）**
- 如果 #6 不跨天用 → **整个删掉**（移出 P1）
- 如果 #8 是同一职责双实现 → 删一套（不抽共享模块）
- 如果 #8 是不同职责 → 移出 P1，留给 Phase 3

**Step 2（确定要做的，2-3 小时）**
- #5：在 `fragments._passes_l*` 失败处加 `logger.debug("L%d_drop reason=%s id=%s", ...)`，不引入 dataclass、不建 sqlite 表
- #7：`_resolve_api_key` 现有 except 拆三个分支，各自 `logger.warning` 打不同 reason，不引入异常体系、不做 boot check

**Step 3（永远不做）**
- #9：现有 `LOGGER.warning` + 跳过下个 plugin 已经够，等真出现"反复 fail 影响日报"再说

#### 5.0.4 V1（原方案）vs V2（整合修复）对比

| 维度 | V1 原方案（5.1-5.5 详细 spec） | V2 整合修复 |
|------|------------------------------|------------|
| 修复条数 | 5 | 2-3（看 spike 结果） |
| 工作量 | ~3 天 | 2 小时 ~ 1 天 |
| 新代码量 | ~400 行 | ~30 行 |
| 新文件 | 2（observability + 共享 gate 模块） | **0** |
| 新 dataclass / 异常 / sqlite 表 | 4 / 3 / 1 | 0 / 0 / 0 |
| 新 CLI 命令 | 1（`kpls metrics`） | 0 |

#### 5.0.5 后续章节 5.1-5.5 的定位

下面的详细 spec **保留作"未来若需要重型方案的备查"**：
- 如果 V2 的 logger.debug 跑一段时间发现"日志噪声大但还是难定位"，再回头取 5.1 的 FilterDecision 方案
- 如果 #8 spike 发现是同一职责，但合并比删除更合适，参考 5.4 的 shared 模块方案
- 不是"5.1-5.5 等于这次的 P1 方案"，是"5.1-5.5 是若干个备选实现的设计文档"

**默认执行路线是 §5.0.3，不是 §5.1-5.5**。

---

### 5.1 P1-#5 过滤原因追踪

#### 问题复述
`fragments.py` 里 L1-L5 五层规则会丢弃事件，但丢弃过程是静默的：没日志、没计数、没快照。事后想知道"为什么这条没进日报"，只能靠重跑+加 print。

#### 根因
规则函数返回 bool（保留 / 丢弃），单一 bit 信息丢失了"哪条规则丢的、阈值是多少、原始事件长什么样"。

#### 方案

引入 `FilterDecision` dataclass：

```python
@dataclass(frozen=True)
class FilterDecision:
    action: Literal["keep", "drop", "modify"]
    rule_id: str          # 例如 "L2_entropy"
    threshold: float | None
    observed: float | None
    original_event: dict  # 仅 drop/modify 时填，避免常态下扩内存
    note: str = ""
```

每条规则函数签名从 `(event) -> bool` 改为 `(event) -> FilterDecision`。

被丢弃的事件落到一张轻量表 `filtered_events`（保留 7 天，定期清理）：

```sql
CREATE TABLE filtered_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    threshold REAL,
    observed REAL,
    event_json TEXT NOT NULL,
    note TEXT
);
CREATE INDEX idx_filtered_events_ts ON filtered_events(timestamp);
CREATE INDEX idx_filtered_events_rule ON filtered_events(rule_id);
```

写入异步化（后台 queue + worker），避免阻塞主流程。

CLI 加查询命令：`keypulse trace filter --since 2d --rule L2_entropy`。

#### 接口
```python
# cleaning/decisions.py
def apply_rule(event: dict, rule: Rule) -> FilterDecision: ...
def write_filter_log(decision: FilterDecision) -> None: ...  # async

# cleaning/fragments.py 改造后
def filter_fragment(event: dict) -> tuple[bool, list[FilterDecision]]:
    """返回 (是否保留, 所有规则的 decision 列表)"""
```

#### 迁移路径
1. 新增 `FilterDecision` 和 `filtered_events` 表（不影响现有逻辑）
2. 一条规则一条规则改造（先从 L1 开始，最简单）
3. 调用方双写：旧的 bool 路径 + 新的 decision 落表
4. 跑一周对比 drop 计数是否一致
5. 切流量：调用方改成只用 decision，旧 bool 删除

#### 工作量
1 天（不含 CLI 命令）。CLI 再 0.5 天。

#### 风险点
- 异步写入失败兜底：`filtered_events` 表写不进就丢，不能反向阻塞主流程
- 表膨胀：每天事件量 ×丢弃率，估算后定保留时长（先 7 天观察）
- 隐私：`event_json` 可能包含敏感数据，落表前要过 P0-#4 的 sanitize_metadata

---

### 5.2 P1-#6 things outline ID 稳定性

#### 问题复述
`thing.id` 当前等于 `hash(LLM 生成的 title)`。LLM 不稳定，今天叫"调试 keypulse 日报"，明天叫"修 keypulse 日报 bug"，hash 完全不同，跨日聚类失效。

#### 根因
ID 不应该依赖 LLM 输出。LLM 是叙述层，ID 是数据层，两个职责混在一起。

#### 方案

`thing.id` 改为基于确定性输入：

```python
def thing_id(date: str, primary_app: str, time_bucket_15min: int, top_actor: str) -> str:
    key = f"{date}|{primary_app}|{time_bucket_15min}|{top_actor}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]
```

LLM 只生成 title 和 narrative 文本，不参与 ID 计算。

跨日聚类用第二层 ID：`thing_lineage_id` = 不带 date 的同样四元组 hash，把不同日期同类的 thing 串起来。

#### 接口

```python
# things/core.py
@dataclass
class Thing:
    id: str             # 当日唯一
    lineage_id: str     # 跨日同源
    date: str
    primary_app: str
    time_bucket: int    # 15min 粒度
    top_actor: str
    title: str          # LLM 生成
    narrative: str      # LLM 生成
```

#### 迁移路径
1. 历史 thing.id 全部失效（这是一次性切换，不做向后兼容）
2. 写迁移脚本读历史 things 表，按新规则重算 id 和 lineage_id
3. 用户感知：第一次运行后跨日合并跳一次（对应历史的"重新归类"）

#### 工作量
0.5 天。

#### 风险点
- 四元组 collision：同一天同一 app 同一 15min 桶同一 actor 出现两次，会被合成一个 thing。实测后如果 collision 多，扩 key 加 `top_event_intent`
- top_actor 计算稳定性：需要保证同一组事件每次算出同一 actor。用"该桶内出现次数最多 + 字典序最小"破平局
- 历史数据迁移：不可逆，先备份 things 表再跑脚本

---

### 5.3 P1-#7 API key 失败语义分级

#### 问题复述
`model.py:242-254` 的 `_resolve_api_key` 把三种本质不同的失败混成一个 None 返回：
1. Keychain 服务不可用（daemon 环境常见）
2. Keychain 里没存这个 key（用户没配）
3. key 存了但 auth 失败（key 过期或写错）

下游拿到 None 传空 key，全部表现为 401，被统一 HTTPError 兜住。用户看到的是"LLM 没响应"，不知道该重新登 Keychain、还是该去配 key、还是该换 key。

#### 根因
返回值用 `Optional[str]` 表达"成功 or 失败"，丢失了失败种类信息。

#### 方案

定义异常层次：

```python
class ApiKeyError(Exception): ...
class KeychainUnavailable(ApiKeyError): ...   # 重试可能恢复
class KeyNotFound(ApiKeyError): ...           # 用户必须配置
class Unauthorized(ApiKeyError): ...          # 用户必须更新

def resolve_api_key(provider: str) -> str:
    """成功返回 key 字符串；失败抛对应子类异常"""
    ...
```

daemon 启动时做 boot check：

```python
# daemon/startup.py
def precheck_api_keys() -> dict[str, ApiKeyStatus]:
    """启动时主动验证一次，结果挂到 daemon health 接口上"""
    ...
```

boot check 异步执行，不阻塞 daemon 启动。但结果出来后日志按级别分：
- `KeychainUnavailable` → WARN，提示用户解锁 Keychain 或用 .env
- `KeyNotFound` → ERROR，提示用户运行 `keypulse setup`
- `Unauthorized` → ERROR，提示用户更新 key

backend selection 改造：`select_backend(require_auth=True)` 时，三种失败都视为不可用，直接走 fallback（如本地模型）。

#### 接口

```python
# backends/registry.py
def select_backend(*, require_auth: bool, fallback: Backend | None = None) -> Backend: ...
# 内部改造：捕获 ApiKeyError 子类，记录失败原因到 backend.last_error
```

#### 迁移路径
1. 新增异常类，`_resolve_api_key` 重命名为 `resolve_api_key` 并改抛异常
2. 调用点逐个迁移（grep `_resolve_api_key` 拿到列表）
3. 加 boot check + status 接口
4. 老的 None 返回路径删除

#### 工作量
0.5 天（不含 fallback 实现）。

#### 风险点
- boot check 慢：用 timeout 5s 兜底，超时归类为 `KeychainUnavailable`
- daemon 启动顺序：boot check 不能在加载用户配置之前跑，要确认 init 序列
- 隐私：异常信息不要带 key 内容

---

### 5.4 P1-#8 bootstrap 质量门槛统一

#### 问题复述
commit 45123a0 给 `obsidian/quality_gate.py` 加了"bootstrap 期跳过门槛"逻辑，但 `pipeline/gates.py` 还在用旧逻辑。两路 gate 对同一天的事件给出不同决策，日报生成节奏出现 flapping：今天通过、明天卡、后天又通过。

#### 根因
质量门槛逻辑在两处独立实现。45123a0 修了一处忘了另一处。属于"双轨制病"在处理层的具体表现。

#### 方案

**方案 A（小修，0.5 天）**：抽共享模块

```python
# quality/bootstrap_aware.py
def is_bootstrap_phase(*, baseline_count: int, threshold: int = 7) -> bool:
    """是否处于 bootstrap 期（baseline 不足）"""
    return baseline_count < threshold

def evaluate_with_bootstrap(score: float, baseline: float | None, *, strict: bool) -> GateDecision:
    """统一的门槛决策函数，两处 gate 都引用"""
    ...
```

`obsidian/quality_gate.py` 和 `pipeline/gates.py` 都改成调这个函数。

**方案 B（彻底，2 天）**：合并为单一 gate 调用

把两套 gate 完全合并成一个 `QualityGate` 类，每个调用方传不同的 `GateConfig`（strictness / baseline_source / threshold）。

**推荐**：先做方案 A 止血。方案 B 留到 Phase 3 注册中心重构时一起做（gate 也属于"散落病"的范畴）。

#### 接口（方案 A）

```python
# quality/bootstrap_aware.py
@dataclass
class GateDecision:
    pass_: bool
    reason: str
    bootstrap: bool

def evaluate_with_bootstrap(
    score: float,
    baseline: float | None,
    *,
    strict: bool,
    baseline_count: int,
) -> GateDecision: ...
```

#### 迁移路径
1. 新增共享模块（不影响现有逻辑）
2. `obsidian/quality_gate.py` 内部改成调共享模块（行为应该一致，对 45123a0 是 no-op）
3. `pipeline/gates.py` 内部改成调共享模块（行为变化，需要回归）
4. 跑一周看 gate 决策一致性

#### 工作量
0.5 天（方案 A）。方案 B 留 Phase 3。

#### 风险点
- 回归：`pipeline/gates.py` 改造后会影响日报生成节奏，需要在 staging 数据上对比一周
- baseline_count 取数源：两处取数源可能不同，需要先确认是同一个数据源再合并

---

### 5.5 P1-#9 plugin read 并发 + 重试

#### 问题复述
`registry.read_all()` 串行调用每个 plugin。一个 plugin 卡 30s，整链路停 30s。异常被 try/except 吞掉，下游不知道哪些 plugin 失败、是临时还是永久。

#### 根因
- 串行：单点放大成全链路
- 异常吞噬：临时故障当永久故障跳过，下次也不重试
- 无超时：单 plugin 可以无限卡

#### 方案

`read_all` 改造为并发 + 超时 + 分级重试：

```python
# sources/registry.py
@dataclass
class ReadResult:
    plugin: str
    events: list[SemanticEvent]
    status: Literal["ok", "partial", "failed_transient", "failed_permanent"]
    error: str | None = None
    duration_ms: int = 0

def read_all(*, timeout_s: float = 30.0, max_retries: int = 3) -> list[ReadResult]:
    """并发调用所有 plugin，每个独立超时和重试"""
    ...
```

实现要点：
- `ThreadPoolExecutor(max_workers=min(8, len(plugins)))`
- 每个任务包装 `_call_with_retry`：
  - `IOError / sqlite.OperationalError` → 指数退避（1s / 4s / 16s），最多 3 次
  - 其他异常 → 不重试，标记 `failed_permanent`
  - 超时 → 标记 `failed_transient`，下一轮 read 时再试
- 返回 `list[ReadResult]`，调用方拿到部分结果 + 失败明细

failed plugin 在元信息里记录，状态接口可见：`keypulse status` 显示"plugin git_log 连续失败 3 次，上次错误：..."。

#### 接口

```python
# sources/registry.py
def read_all(*, timeout_s: float = 30.0, max_retries: int = 3) -> list[ReadResult]: ...

# 调用方改造
results = registry.read_all()
events = [e for r in results if r.status in ("ok", "partial") for e in r.events]
failed = [r for r in results if r.status.startswith("failed")]
log_plugin_failures(failed)
```

#### 迁移路径
1. 新增 `ReadResult` 和并发版 `read_all`，旧版改名 `_read_all_serial` 保留兜底
2. 调用方改用新接口
3. 在 staging 跑一周观察并发稳定性
4. 删掉串行版

#### 工作量
1 天。

#### 风险点
- **sqlite 并发锁**：多个 plugin 同时读 sqlite 文件，已有 WAL 但 `busy_timeout` 默认 0。需要在 plugin 打开 sqlite 时设 `PRAGMA busy_timeout=5000`
- **资源耗尽**：max_workers 设 8 是经验值，需要观察。CPU 弱的机器可能要降到 4
- **重试雪崩**：同时多个 plugin 都失败时，指数退避可能让重试集中。给每个 plugin 的退避加 ±20% jitter
- **mac sandbox**：某些 plugin 走 macOS API 可能在子线程里失败（主线程 only），需要 case by case 测试，必要时退回串行

---

## 六、决策记录

| 决策 | 内容 | 决策人 | 日期 |
|-----|------|-------|------|
| Review 主席 | Opus（claude-opus-4-7）做交叉对比与排序 | 用户 | 2026-04-30 |
| Scout 模型 | 三路并行使用 Haiku（claude-haiku-4-5）做层级 explore | 用户 | 2026-04-30 |
| cleaning-unify 合并节奏 | 先出本次 review，再用真实数据验证 Step 1 一周，通过后才合 main | 用户 | 2026-04-30 |
| P0 实现责任 | 4 条 P0 由 Opus 亲手实现，不委托。理由：质量优先 | 用户 | 2026-04-30 |
| P0 分支策略 | `fix/p0-stability`，每条 P0 独立 commit | 主席 | 2026-04-30 |
| P1 实现责任 | 待 P0 落地后另行决策（可能委托 Codex MCP / Sonnet） | 待定 | — |
| 文档定位 | 本文档作为项目长期参考资料保留，不删 | 用户 | 2026-04-30 |

### 待决策事项

- [ ] P1 是 4 条全做还是只做 #5 / #7 / #9 三条核心？（#6 #8 是优化，不是阻塞）
- [ ] Phase 3 注册中心重构是否要单独立 epic？什么时候启动？
- [ ] privacy max_tier 默认值定 yellow 还是 green？（影响用户首次体验）

### 不在本次 review 范围内的事

- UI / CLI 体验优化
- 新数据源接入（参见已有 backlog）
- 性能（除非和稳健性相关，如 P1-#9）
- 文档完善
