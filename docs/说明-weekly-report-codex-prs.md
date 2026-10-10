# 周报方案 · Codex 实施 brief

**目标读者**:Codex MCP(gpt-5.4 / 5.3-codex 级别模型),负责按本文档拆 PR 实施

**配套**:本文档与 `docs/weekly-report-design.md` 是**配套**关系,设计稿是 What,本稿是 How。Codex 实施时**两份必须一起读**。

**作者**:Harland + Claude(Opus)
**首版**:2026-05-06
**状态**:设计稿轮 3 已定稿,等开工

---

## 0 · 你需要先读什么

按这个顺序读完才动手:

1. **`docs/weekly-report-design.md` 全文**(900+ 行,二阶段唯一来源)
   - 重点:§4.2(证据分层)、§4.3(token 估算)、§4.6(采集字段现状)、§7(M1 范围)、§9 已定决策、§10 PR 顺序、**§11 全章(基础设施)**
2. **`~/.keypulse/log.md`**(`2026-05-06` 那条 codex 摸底报告)—— 字段现状证据
3. **`CLAUDE.md`** 项目根 —— 工作原则(只改需要改的、错误处理具体、引入新依赖说明理由)

读完后开工。**不要跳过 §11**,所有 PR 依赖 PR-1 的基础设施。

---

## 1 · 总体路线

6 个 PR,顺序与依赖如下:

```
PR-1 基础设施 ──┬─→ PR0 M0 数据基础 ──┐
                │                       ├─→ PR2 聚类管线 daily ─→ PR3 Weekly 生成器 ─→ PR4 HUD banner
                └─→ PR1 Topics 清理 ───┘
                                                                                         
                (PR0 / PR1 可并行,但都依赖 PR-1)
```

**强制约束**:

- 每个 PR 必须独立可合并、独立可回滚
- 每个 PR 必须有完整测试(单元 + 集成,集成测试用真实 SQLite 不 mock)
- 不要把多个 PR 合并成大 PR(diff 难审)
- 不要在某个 PR 里顺手改无关代码

---

## 2 · PR-1 · 基础设施(横切关注点)

### 目标

为后续所有 LLM 调用提供:cache、cost tracking、prompt 版本管理、typed JSON schema、daily-summary 中间态、模型档位 config。

设计稿对应:**§11 全章**

### 任务清单

#### 2.1 模型档位 config

文件:`keypulse/config.py`、`config.toml`(模板) + `~/.keypulse/config.toml`(运行时)

新增 `[llm]` 段:

```toml
[llm]
tier = "mini"  # mini | standard | premium,默认 mini
provider = ""  # 可选,留空走档位默认
monthly_budget_usd = 5.0  # 软警告阈值
local_ollama_url = "http://localhost:11434"  # mini 本地默认
```

档位到模型的默认映射(可被 provider 覆盖):

| tier | provider 默认 | model 默认 |
|---|---|---|
| mini | `ollama_local` | `qwen2.5:7b` |
| standard | `deepseek` | `deepseek-chat` |
| premium | `anthropic` | `claude-sonnet-4-6` |

#### 2.2 LLM Gateway 重构

文件:`keypulse/pipeline/model.py`(已有 ModelGateway,扩展它)

新增能力:

1. **统一 call 接口**:`gateway.call(capability, prompt, schema=None, max_tokens=N, temperature=T)`,内部按 capability 选档位 + 模型
2. **Cache 层**(`~/.keypulse/cache/llm/{sha256}.json`):
   - cache key = `sha256(stable_serialize({capability, prompt_version, prompt, model, input_data}))`
   - 命中:直接返回 cached output,不调 LLM,但仍写 cost.jsonl(`cache_hit: true`,cost 记 0)
   - 未命中:调 LLM,写 cache + cost.jsonl
   - TTL 30 天,过期文件由 `keypulse housekeep` 定期清(M1 跑一次启动时清即可)
3. **Cost tracking**(`~/.keypulse/cost.jsonl` append-only):见 §11.6 schema
4. **Retry 策略**:每个 capability 默认 retry 2 次,失败后由调用方 fallback(不在 gateway 层做 fallback,因为各 capability 降级方式不同)
5. **Prompt prefix caching**:Anthropic / OpenAI 的 cache_control header 自动加(prompt 前 N 个固定块)
6. **Schema 验证**:如果调用方传了 `schema`,LLM 输出走 jsonschema / pydantic 验证,验证失败计 retry 一次

#### 2.3 Prompt 版本管理

新增目录:`keypulse/prompts/`

```
keypulse/prompts/
├── L1_cluster_review.v1.md
├── L2_narrative.v1.md
├── L3_topic_naming.v1.md
├── L4_weekly_reconcile.v1.md
├── L5_weekly_main_narrative.v1.md
└── L6_explorer.v1.md
```

每个 prompt 文件首部 frontmatter:

```yaml
---
capability: L1_cluster_review
version: v1
model_tier: standard
input_schema: schemas/L1_input.json
output_schema: schemas/L1_output.json
max_tokens: 800
temperature: 0.2
---
```

加载器:`keypulse/prompts/loader.py` 提供 `load_prompt(capability) → PromptSpec`,gateway.call 自动用 spec 的 schema/max_tokens/tier。

PR-1 仅创建 L1-L6 占位文件 + frontmatter,**实际 prompt 正文由 PR2/PR3 写**(因为 prompt 跟具体聚类逻辑耦合)。

#### 2.4 Daily-summary 中间态

文件:新增 `keypulse/pipeline/daily_summary.py`

- 写入函数:`write_daily_summary(date, clusters, misc, topic_snapshot, cost)` → `~/.keypulse/daily-summary/{date}.json`
- 读取函数:`read_daily_summary(date)` → dict 或 None
- schema 见设计稿 §11.5

#### 2.5 Cost CLI

`keypulse cost` 子命令(扩展 `keypulse/cli.py`):

```bash
$ keypulse cost
本月(2026-05):$0.07
本周(W18):$0.02
按 capability:
  L1_cluster_review  $0.04  (54 calls, 12 cache hits)
  L2_narrative       $0.02  (132 calls, 38 cache hits)
  ...
```

`keypulse cost --week 2026-W18` / `--month 2026-05` 支持指定区间。

#### 2.6 HUD 集成

文件:`keypulse/hud/summary.py` + `keypulse/hud/monitor_html.py`

- HUD 顶栏新增 `💰 本月 $X.XX` 字段(从 cost.jsonl 滑窗 30 天读取)
- 接近 `monthly_budget_usd` 80% 时改黄字,达到 100% 改红字 + 提示

### 验收

完整列表见设计稿 §11.8。**所有项必须打勾**才合并。关键:

- 单元测试:cache 命中/未命中、TTL 过期、cost.jsonl 写入正确性、prompt_version 加载、schema 验证失败 retry
- 集成测试:跑一次 mock 的 ClusterReview(用 stub 模型,验证全链路)
- 兼容性:不动现有 daily 生成路径(things.py 里那条),只新增 gateway 能力

### 注意事项

- **不**实现具体的 L1-L6 prompt 正文(PR2/PR3 做)
- **不**改 daily 生成现有逻辑(things.py 保持原样,等 PR2 替换)
- **不**做 M2 的 capability 化大重构,只在数据格式上"为 M2 留口子"(见 §11.7)

---

## 3 · PR0 · M0 数据基础

### 目标

修复 `sources/` 已采字段未入 `raw_events` 的断层。让聚类管线可以从 raw_events 读到硬证据。

设计稿对应:**§4.6、§7 M0 前置**

### 任务清单

#### 3.1 sources → raw_events 写入 sink

文件:`keypulse/sources/__init__.py` 或新增 `keypulse/sources/sink.py`

需要把以下 source 产出的 `SemanticEvent` 落到 `raw_events`(通过 `insert_raw_event`):

- `git_log.py` —— commit hash → `metadata.entities.commit_hash`、repo path → `metadata.entities.file_paths`
- `claude_code.py` —— session_id → `metadata.entities.session_id`、project_dir → `metadata.entities.file_paths`
- `codex_cli.py` —— session_id → `metadata.entities.session_id`
- `markdown_vault.py` —— file path → `metadata.entities.file_paths`
- `chrome_history.py` —— url(已去 query) → `metadata.entities.urls`
- `safari_history.py` —— url(已去 query) → `metadata.entities.urls`

#### 3.2 raw_events.metadata_json 规约 entities 子字段

新约定 `metadata_json.entities` 子对象:

```json
{
  "entities": {
    "commit_hash": "21c290d...",
    "file_paths": ["keypulse/hud/summary.py"],
    "urls": ["https://github.com/foo/bar"],
    "session_id": "claude-2026-05-06-abc",
    "app_bundle_id": "com.todesktop.230313mzl4w4u92"
  }
}
```

约定文件:`docs/raw-events-metadata-schema.md`(新增,简短描述 schema)。

#### 3.3 entity_extractor 落库

文件:`keypulse/quality/entity_extractor.py`

当前抽取的实体只在运行时用。改成:每次 `insert_raw_event` 前,调用 entity_extractor,把抽到的命名实体落 `metadata.entities.named_entities`(列表,去重后)。

注意:这条改动会让 raw_events 表写入路径变重,需要 benchmark 一次,确保不显著拖慢采集 watcher。如果确认有明显性能影响,改成异步后处理(M0 接受 daily 第一次跑前补完即可)。

#### 3.4 Chrome tab url 去 query

文件:`keypulse/capture/policy.py`(当前只 redact 敏感 query) + `keypulse/capture/normalizer.py`

聚类用的 url 必须去 query。在 raw_events 写入时,把 `metadata.entities.urls` 里的 url 统一去 query;**保留** `content_text`(展示用)和 `metadata.url`(原始,debug 用)各自不动。

#### 3.5 Migration 脚本

新增 `scripts/migrate_v0_entities.py`:对历史 raw_events 跑一遍,把 `metadata_json` 里能解析出的 entities 补上(commit hash 用正则、url 去 query)。**不动**已有字段。

dry-run 模式默认开启,确认 OK 再 `--apply`。

### 验收

- [ ] `raw_events.metadata_json` 新事件均带 `entities` 子对象(at least timestamp + 至少一个 entity 字段)
- [ ] git/Claude/Codex/markdown/chrome/safari 6 个 source 的事件都能落 raw_events 并被 SQL 查到
- [ ] `migrate_v0_entities.py` 跑一次,历史事件补上 entities(成功率报告)
- [ ] 现有 daily 生成路径(things.py)**不变**,daily.md 输出与 PR0 之前一致(回归测试)
- [ ] 采集 watcher TPS 不显著下降(±10% 以内)
- [ ] 单元测试:6 个 source 的 SemanticEvent → raw_events 转换、entity_extractor 落库、url 去 query

### 与 PR1 的并行

PR0 和 PR1 操作的代码区域不重叠(PR1 在 obsidian 渲染层),可并行开发,合并时按 PR0 → PR1 顺序合(PR1 不依赖 PR0 数据,但合并顺序减少 conflict)。

---

## 4 · PR1 · Topics 清理 + .keypulse/ 迁移

### 目标

清理现有 80 张 Topics 卡的空壳,把 Events/Topics 迁到 `.keypulse/` 隐藏目录。

设计稿对应:**§3 数据架构、§7 M1 主体**

### 任务清单

#### 4.1 Topics 空壳 lint 脚本

新增 `scripts/lint_topics.py`:

- 扫描 `<vault>/Topics/` 下所有 `.md` 文件
- 分类:
  - 0 字节 → 标 `delete_candidate`
  - 仅 frontmatter 无内容 → 标 `delete_candidate`
  - 链接关联事件 < 2 条 → 标 `merge_candidate`
  - 链接关联事件 ≥ 2 条但无 keywords/无 narrative → 标 `enrich_candidate`
  - 健康(有 keywords + narrative + ≥ 2 events) → 保留
- 输出 dry-run 报告(JSON + 人类可读 markdown)
- `--apply` 真删/真合并(先备份整个 Topics/ 到 `<vault>/.keypulse-backup/topics-2026-05-06.tar.gz`)

#### 4.2 Migration 脚本:Events/Topics → .keypulse/

新增 `scripts/migrate_to_keypulse_dir.py`:

- 把 `<vault>/Events/` 整体移到 `~/.keypulse/events/`
- 把 `<vault>/Topics/`(lint 后)整体移到 `~/.keypulse/topics/`
- 修正 Daily/Weekly 里的 `[[Events/...]]` `[[Topics/...]]` wiki link 路径
- 默认 dry-run,`--apply` 实际执行,执行前先备份 vault

#### 4.3 Obsidian exporter 更新

文件:`keypulse/obsidian/exporter.py`

- 写入 events/topics 的目标目录从 `<vault>/Events`/`<vault>/Topics` 改为 `~/.keypulse/events`/`~/.keypulse/topics`
- daily/weekly 里生成的 wiki link 改成 `[[../.keypulse/events/...]]` 或文件级 absolute(待定,需测试 Obsidian 对绝对路径的支持)
  - **若 Obsidian 不支持 vault 外的 wiki link**:在 daily/weekly 里改用普通 markdown link `[文本](file:///Users/Harland/.keypulse/events/...)`
  - 决策点:实施时先测两种方案在 Obsidian 中的实际效果,哪种自然就用哪种

### 验收

- [ ] `lint_topics.py` dry-run 输出报告,Harland 人工 review 后 `--apply`
- [ ] `migrate_to_keypulse_dir.py` 备份 → 迁移 → wiki link 修正,Obsidian 中无死链
- [ ] 新事件直接写到 `~/.keypulse/events/`(exporter 更新生效)
- [ ] 用户在 Obsidian 中**只看到** Daily/Weekly 两个目录(.keypulse 在 vault 外,不索引)

### 注意事项

- **必须**先备份再迁移,失败必须能回滚
- 迁移期间停 keypulse daemon(避免写冲突):`launchctl unload ... && migrate && launchctl load ...`
- **不要**动 Projects/(用户手动维护,设计稿 §9 已定不纳入)

---

## 5 · PR2 · 聚类管线 daily(核心)

### 目标

实现 daily 阶段的硬证据建图 + L1/L2/L3 三次 LLM 调用 + topics 持久化 + Daily「今日主线」回写。

设计稿对应:**§4.2 证据分层、§4.3 方案 C、§5.1 prompt、§6.1 数据流、§11.3 调用点**

### 任务清单

#### 5.1 硬证据建图

新增 `keypulse/pipeline/clustering.py`:

```python
def build_evidence_graph(events: list[Event]) -> dict[event_id, set[event_id]]:
    """
    输入: 当日 events(已带 metadata.entities)
    输出: 邻接表(事件对 → 是否连边)
    
    建图规则(详见设计稿 §4.2):
      H1: 共享 entity_id(commit/file_path/url/named_entity)
      H2: 共采集源(session_id / app+window / chrome_tab)
      H3: Δt < 5min 直连;5–30min 需 H1 或 H2 加成;>30min 仅强证据
    """

def connected_components(graph) -> list[set[event_id]]:
    """连通分量 = 初始聚类候选"""

def detect_merge_candidates(components, threshold) -> list[tuple[id, id]]:
    """
    跨分量合并候选(给 LLM 二次裁决)
    M1 不用 embedding(§4.2 已定),仅用关键词/实体重叠 jaccard
    """
```

单元测试覆盖:H1/H2/H3 各自命中、加成命中、不命中、跨分量 merge_candidate。

#### 5.2 L1 ClusterReview prompt

文件:`keypulse/prompts/L1_cluster_review.v1.md`

实施 §5.1 设计稿的 prompt 内容,改成 typed JSON schema 输出(裁决 + topic 匹配,**不写叙事**)。

输出 schema:

```json
{
  "type": "object",
  "properties": {
    "clusters": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["component_id", "topic_action"],
        "properties": {
          "component_id": {"type": "string"},
          "topic_action": {"enum": ["existing", "new", "misc"]},
          "topic_slug": {"type": "string"},
          "merge_with_component": {"type": "string"},
          "reason": {"type": "string"}
        }
      }
    },
    "misc_event_ids": {"type": "array", "items": {"type": "string"}}
  }
}
```

#### 5.3 L2 Narrative prompt

文件:`keypulse/prompts/L2_narrative.v1.md`

每聚类一次,输出 80-150 字的段落叙事。模型档:mini(§11.1 已定 L2 自动降档)。

输入:单个聚类的事件 + topic 上下文。
输出:markdown 段落(无 H3,只是段内文本,H3 由后处理拼)。

#### 5.4 L3 TopicNaming prompt

文件:`keypulse/prompts/L3_topic_naming.v1.md`

仅当 L1 决定开新 topic 时调用。输入:该聚类事件 + 现有 slug 集合(去重)。输出 JSON `{slug, display_name, keywords[5-10]}`。

#### 5.5 编排器

新增 `keypulse/pipeline/daily_orchestrator.py`:

```python
def run_daily(date_str, *, trigger="18:00") -> DailySummary:
    """
    1. query raw_events for date
    2. filter / normalize
    3. build_evidence_graph → connected_components → merge_candidates
    4. 空日跳过(< 3 events)
    5. 加载 topics 索引 → 裁剪(hot + 关键词命中,上限 30,详见 §11.2 #2)
    6. ★ L1 ClusterReview 调用(typed JSON 输出)
    7. ★ L2 Narrative × N 聚类(并行调用,各自 mini 档)
    8. ★ L3 TopicNaming × M 新主题(并行)
    9. 后处理:
       - upsert .keypulse/topics/{slug}.md(typed diff,不全量重写)
       - 渲染 Daily/{date}.md ## 今日主线 段(替换 things.py 路径,things.py 整段保留备用)
       - 写 .keypulse/daily-summary/{date}.json
       - 刷新 .keypulse/hot.md
       - append .keypulse/log.md(本次决策记录)
    """
```

**注意**:23:30 收尾走同一个 orchestrator,但只送 pending events(`§11.2 手段 #5`)。

#### 5.6 launchd 触发

新增/扩展 `keypulse/cli.py` 的 `daily` 子命令 + 写一个 `com.keypulse.daily.plist`:

- 18:00 触发 `keypulse daily run --trigger 18:00`
- 23:30 触发 `keypulse daily run --trigger 23:30`
- 异步:LLM 调用走 ThreadPoolExecutor,不阻塞 HUD

### 验收

- [ ] 跑 5/6 真实 daily 数据,L1/L2/L3 全链路成功
- [ ] daily.md 「今日主线」段产出 ≥ 3 个真主题(对比当前空壳 1-2 条)
- [ ] `.keypulse/topics/` 有 typed diff 写入,旧 topic 追加 entry,新 topic 创建文件
- [ ] `.keypulse/daily-summary/{date}.json` 有效
- [ ] `cost.jsonl` 该日有 L1/L2×N/L3×M 记录
- [ ] retry 测试:mock LLM 失败 2 次,fallback 走规则层占位
- [ ] 集成测试:hot.md / log.md / topics.md 三方一致
- [ ] HUD 上屏「今日主线」三条,跳转锚点正确
- [ ] launchd 18:00 / 23:30 触发,异步不阻塞 HUD

### 注意事项

- **保留** `keypulse/pipeline/things.py`(当前 daily 生成路径)作为 fallback,daily_orchestrator 失败后切回(M1 期间过渡保险)
- **不要**改 narrative_v2/skeleton.py 那条老路径(deprecated,M2 删)
- 23:30 收尾的 LLM 调用 cache key 必须包含 trigger 字段,跟 18:00 区分

---

## 6 · PR3 · Weekly 生成器

### 目标

实现 weekly 阶段:L4 reconcile + L5 客观记录 + L6 探索者 + 渲染。

设计稿对应:**§4.5 weekly 算法、§5.2-5.4 prompt、§6.2 数据流、§11.3 L4-L6**

### 任务清单

#### 6.1 主题状态计算(规则层)

新增 `keypulse/pipeline/topic_status.py`:

实施 §4.4 状态打标 + §4.5 加速度计算。规则层,不调 LLM。

#### 6.2 L4-L6 prompt

- `prompts/L4_weekly_reconcile.v1.md`(§5.4)
- `prompts/L5_weekly_main_narrative.v1.md`(§5.3)
- `prompts/L6_explorer.v1.md`(§5.2)

#### 6.3 Weekly 编排器

新增 `keypulse/pipeline/weekly_orchestrator.py`:

```python
def run_weekly(week_str) -> str:
    """
    1. 阈值检查(本周 daily 数 ≥ 5,否则走 fallback)
    2. 读 7 天 daily-summary(§11.5,不 reparse markdown)
    3. 算 topic_status(规则层)
    4. 收集 merge_candidate_pairs + 算亲和度
    5. ★ L4 WeeklyReconcile(typed JSON,score ≥ 5 才送)
    6. 应用 L4 merge 决策到 .keypulse/topics/
    7. 选 top N(N = min(8, 非 misc 主题数),§9 已定)
    8. ★ L5 WeeklyMainNarrative + ★ L6 Explorer 并行
    9. 渲染 Weekly/{week}.md(§2.3 双板块顺序排列)
    10. 写 cost.jsonl + log.md
    """
```

#### 6.4 launchd 触发

`com.keypulse.weekly.plist` 周五 23:35 触发(§6.2 已定)。

#### 6.5 fallback HUD

阈值不达标(< 5 天 daily)→ HUD banner 一行:"本周数据不足,周报跳过"。

### 验收

- [ ] 跑一周真实数据,L4-L6 全链路成功
- [ ] weekly.md 双板块输出格式正确(§2.3)
- [ ] L5/L6 并行调用(observe wallclock 时间)
- [ ] retry 测试 + fallback 测试
- [ ] weekly 顶部"本期成本"渲染正确

---

## 7 · PR4 · HUD banner + Events 文件名规范化

### 目标

HUD 顶栏加 weekly 触达 banner + Events 文件名"片段-"前缀取消 + Events 标题人话化。

设计稿对应:**§6.3 触达、§7 主体**

### 任务清单

#### 7.1 HUD weekly banner

文件:`keypulse/hud/summary.py` + `monitor_html.py`

- 周五 23:40 后到下周一,HUD 顶栏显示"本周回声 →"banner
- 点击跳转 `obsidian://...&file=Weekly/{week}.md`
- **跳转成功后永久隐藏**(本周不再显示),失败不隐藏
- 跳转成功检测:见 §HUD 设计已定,失败兜底 = 接受不可靠 + 用户下次打开自然不见

#### 7.2 Events 文件名规范化

文件:`keypulse/obsidian/exporter.py`

- 取消"片段-"前缀(§Obsidian 现场审计 #4 已定)
- 标题改成"actor + intent + 关键词"格式(§3.3 已定)
- 例:`1123-make-app-实际成功了-bundle-已在-dist-preflight-在-venv-没装-8b907d77.md` → `1123-build-app-bundle-成功-preflight-跳过.md`
- 历史卡保留 `id` frontmatter(§9 已定不动历史)

#### 7.3 Events 标题人话化

如有需要(标题机器味重),走 mini 档 LLM 重命名(单事件 cost 极低)。可选,M1 时间紧可推 M2。

### 验收

- [ ] HUD banner 周五出现,点击跳转,成功后隐藏
- [ ] 新事件文件名无"片段-"前缀
- [ ] 标题人话化(若实现)单元测试覆盖

---

## 8 · 通用约束(所有 PR)

### 8.1 测试

- 单元测试覆盖率新代码 ≥ 80%
- 集成测试用真实 SQLite + tempdir,不 mock
- LLM 调用集成测试用 stub gateway(`MOCK_LLM=1` env var 控制)

### 8.2 代码风格

- 跟 CLAUDE.md 项目根的工作原则一致
- 错误处理具体,禁止裸 except
- 引入新依赖前在 PR description 说明理由
- 不顺手重构无关代码

### 8.3 文档

- 每个 PR 在 description 里贴本文档对应章节(`§N.M`)
- 改动了 schema/config 的,同步更新 `docs/weekly-report-design.md` 相关章节
- M2 留口子的代码加 `# M2: ...` 注释(便于未来检索)

### 8.4 不要做的

- ❌ 不实现 M2 任务(annotation / trend / lint / query / multi-vault / data export)
- ❌ 不引入 embedding 库(§1 已定,§4.2 S1 已删)
- ❌ 不重构 capabilities/ 模块(M2 才做)
- ❌ 不动 Projects/(§9 已定不纳入)
- ❌ 不删 things.py(M1 期间 daily fallback 用)
- ❌ 不在 PR-1 写具体 prompt(占位即可,PR2/PR3 才写)

### 8.5 性能基线(M1 验收)

- daily 18:00 跑完 < 30s(包括 LLM)
- daily 增量 < 1s(无 LLM)
- weekly < 60s(包括 LLM)
- HUD 刷新 < 200ms
- 采集 watcher TPS 不下降 ±10%

### 8.6 失败追溯

每次 LLM 调用失败的输入/输出/error 写到 `~/.keypulse/log.md`,用于 4 周后调阈值。

---

## 9 · 模型选型(开工前 Harland 确认一次)

PR-1 开工前,Harland 用以下命令本地跑一次,确认 mini 档可用:

```bash
ollama pull qwen2.5:7b
ollama run qwen2.5:7b "你好,简单介绍下自己"
```

如果机器跑不动 7B,降到 `qwen2.5:3b`(改 config 默认即可)。

standard 档默认 DeepSeek v3,需要用户在 `~/.keypulse/config.toml` 设 `[llm.deepseek] api_key = "..."`。

---

## 10 · 完成判据

所有 6 个 PR 合并后,跑一周真实数据,达成以下:

- [ ] 每天 18:00 + 23:30 自动产 daily,「今日主线」段有真主题(非空壳)
- [ ] 周五 23:35 自动产 weekly,双板块输出
- [ ] HUD 顶栏显示成本 + 周末 banner
- [ ] `keypulse cost` CLI 输出本月成本 < $1(standard 档)
- [ ] 无未捕获异常,log.md 干净
- [ ] Obsidian 中只见 Daily/Weekly,不见 Events/Topics

达成 → 进入 4 周 dogfood 期 → 决定 M2 范围。
