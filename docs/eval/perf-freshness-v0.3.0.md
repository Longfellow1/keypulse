# 性能与新鲜度基准 v0.3.0（合成数据，Eval PRD E5）

> 范围：#33 中可以无人值守完成的部分。只用合成数据，不含真实用户内容。依据 `docs/Penpal_Eval_PRD.md` §3.5 测法与 §5 通过线。复现脚本：`scripts/bench/perf_freshness.py`。

## 结论

| 指标 | 实测 P95 | PRD 通过线 | 判定 |
|---|---|---|---|
| M5 查询时延（10 万条，热） | 0.19 s | ≤ 2 s | **PASS** |
| M5 查询时延（10 万条，冷） | 0.38 s | ≤ 2 s | **PASS** |
| M4 动态活动新鲜度 | 4.85 s | ≤ 30 s | **PASS** |
| M4 结构化源新鲜度 | 0.14 s（只含同步执行，见下） | ≤ 5 分钟 | **PASS（有条件，不能直接采信）** |

1. **检索侧性能有余量。** 10 万条下查询 P95 冷/热都在 0.4 s 以内，线为 2 s。时延随结果数和词频变化，但最慢的一类（多词、时间过滤）也只有 0.23 s 和 0.16 s（热）。
2. **动态活动新鲜度达标，但与 flush 间隔绑定。** 4.85 s 的主要来源是采集队列的 flush 间隔（默认 5 s）。这是配置项，不是检索慢。
3. **结构化源新鲜度的 PASS 不能直接采信，这是本次最大风险。** 基准测的是"同步已执行 → 可检索"，只有 0.14 s。PRD 的 M4 指的是从源数据产生到可检索的时间，中间还隔着调度器。默认配置下，zsh、Claude Code、Codex、git 等 `liveness = "always"` 的源按 `poll_interval_sec = 600`（10 分钟）轮询，`debounce_sec = 300`。这意味着最坏情况约 10 分钟才会被同步，超出 5 分钟线。**这是配置决定的，不是检索慢造成的；本 PR 不调整代码。** 需要负责人先确认 M4 的口径是否包含调度等待（见"建议"）。
4. **参考机器尚未指定。** PRD §1.4 写的是"待工程指定"，本次在开发机上跑。以上 PASS 只能说明"在这台机器上过线"，不能替代参考机器判定。

## 环境

| 项 | 值 |
|---|---|
| 机器型号 | Mac16,8（`sysctl -n hw.model`） |
| CPU / 内存 | Apple M4 Pro / 24 GiB |
| macOS | 26.7（build 25G229） |
| Python（基准运行） | 3.11.15 |
| SQLite | 3.54.1 |
| 基线 commit | 79d5c8e（PR 在其上新增 `scripts/bench/perf_freshness.py`、`tests/test_perf_freshness.py`，并在 `pyproject.toml` 注册 `slow` marker） |
| 随机种子 | 20261024 |
| 夹具 digest | 61d9dc7581d2aeb5（同 seed 同 digest，测试中校验） |
| 运行命令 | `python scripts/bench/perf_freshness.py --events 100000` |

## 夹具

- 10 万条事件，时间跨度 365 天，锚点 2026-10-24（固定，不随当天日期变化）。
- 来源权重：claude_code 30%、zsh 15%、chrome 12%、safari 10%、codex 10%、git 8%、clipboard 15%。
- 文本长度：zsh 2–7 词，git 4–20 词，浏览器标题 3–12 词，剪贴板 3–60 词，对话 15–120 词（约 2% 为 300–600 词的长文）。约 15% 的 token 为中文。
- 植入 40 个标记词，各出现 50 次，用于精确查询和部分线索查询。
- 写入路径：语义源走 `persist_semantic_event`（与 `penpal sources sync` 同一个 sink，实体回填同步执行）；剪贴板走 `insert_raw_event`。
- 结果：100,000 行，DB 148.9 MB，约 1,489 字节/条。占用最大的是 `raw_events_fts_data`（61 MB，trigram 全文索引）和 `raw_events` 表（60 MB）。
- 导入耗时 77.7 s（约 1,287 条/s，每条独立提交）。这是信息项，不是 PRD 指标。

## 结果

### 查询时延（search_context，经 MCP 服务层，真实授权）

| 口径 | n | P50 | P95 | Max |
|---|---|---|---|---|
| 热（预热后，30 题 × 5 轮） | 150 | 0.110 s | 0.193 s | 0.235 s |
| 冷（每次新 SQLite 连接，30 题各 1 次） | 30 | 0.115 s | 0.376 s | 0.384 s |

热 P95 按类别：精确词 0.10 s，多词 0.23 s，部分线索 0.12 s，时间过滤 0.16 s，来源过滤 0.09 s，零命中 0.13 s。

Sanity 检查通过：零命中题全部返回 `no_results`，精确词题全部有命中。

### 其他接口（热，同一会话）

| 接口 | n | P50 | P95 | Max | 备注 |
|---|---|---|---|---|---|
| get_timeline | 20 | 0.190 s | 0.199 s | 0.202 s | 一半 1 天窗口、一半 7 天窗口，默认 limit 50 |
| get_evidence | 10 | 0.0004 s | 0.0007 s | 0.0007 s | 样本只有 10 条（均为剪贴板证据，见限制 6） |

### 新鲜度

| 指标 | n | P50 | P95 | Max | 线 | 判定 |
|---|---|---|---|---|---|---|
| 结构化源（zsh 追加一条 → 真实 sync → 可检索） | 20 | 0.124 s | 0.137 s | 0.139 s | ≤ 300 s | PASS（不含调度等待） |
| 动态活动（剪贴板事件入 CaptureManager 队列，真实 flush 线程） | 20 | 4.14 s | 4.85 s | 4.86 s | ≤ 30 s | PASS |

## 限制（读数字前必看）

1. **查询数是 30，不是 PRD 的 100。** 按本次任务要求取 30 题（精确、多词、部分线索、时间过滤、来源过滤、零命中各 5 题）。冷样本 30 个、热样本 150 个。P95 的口径与 PRD §3.5 不完全一致，正式基准要补到 100 题。
2. **"冷"不是真正的冷启动。** 每次调用前关闭并重开 SQLite 连接，清空的是 SQLite 页缓存。没有清 OS 页缓存（需要 sudo `purge`），所以没有测重启后的冷启动。
3. **时延是进程内 MCP 会话的调用耗时。** 不含 stdio 管道开销，也不含 Host 推理（PRD 要求不含 Host 推理，这一点符合）。
4. **结构化源新鲜度只测到同步完成。** 没有测调度器的等待时间，因此不能代表端到端的 M4。见结论 3。
5. **动态新鲜度的入口是 CaptureManager 队列，不是 OS 事件。** 真实的 flush 线程在运行，但事件是直接放进队列的，没有经过 watcher。等待时长主要由 `flush_interval_sec`（默认 5 s）决定。
6. **get_evidence 只测了 10 条剪贴板证据。** 语义源的夹具没有写 `evidence_locator`，所以文件型证据（指向真实文件行）没有被测到。
7. **单次运行。** 10 万条只跑了一次（按要求），没有评估方差；结构化和动态新鲜度各 20 个样本。
8. **版本说明。** 基准用 Python 3.11 运行。全量测试用 Python 3.13 运行，因为 3.11 无法解析 `penpal/hud/app.py` 中 f-string 内的反斜杠（这个问题已存在于 base commit，CI 使用 3.12，不受影响）。

## 验证

- `python -m pytest -q`：1192 passed，17 skipped，1 xfailed（Python 3.13）。
- `lint-imports`：3 个契约全部保留（kept），0 个失败。
- `PENPAL_RUN_SLOW=1 python -m pytest -q -m slow tests/test_perf_freshness.py`：2 passed（600 条小规模端到端）。不设变量时默认 skip。

## 建议

1. **先确认 M4 结构化源的口径。** 负责人需要决定：M4 是否包含调度等待。如果包含，当前默认配置（poll 600 s、debounce 300 s）大概率不满足 5 分钟线。
2. **如果口径包含调度等待，下一步是实测调度器的端到端延迟**（单独一个 PR，用 `SourcesScheduler` 而不是直接调 sync），再决定是调短轮询间隔，还是为结构化源加文件变化触发。在此之前不要把结构化源的 M4 判为 PASS。
3. **确认 M5 的冷/热口径。** PRD 没有区分。本报告两个都判了，都在线内。
4. **指定参考机器**，并在参考机器上重跑一次。
5. **补齐到 100 题**，并加入文件型证据的 get_evidence 样本。

## 复现

```
python scripts/bench/perf_freshness.py --events 100000 --out <路径>.json   # 全量
PENPAL_RUN_SLOW=1 python -m pytest -q -m slow tests/test_perf_freshness.py  # 小规模冒烟
```

脚本把 HOME 和工作目录都重定向到临时目录，不读写真实用户数据。夹具由 seed 生成，同一 seed 同一 digest。
