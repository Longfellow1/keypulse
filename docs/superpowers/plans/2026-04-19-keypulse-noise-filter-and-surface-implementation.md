# KeyPulse Noise Filter And Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 KeyPulse 的 Obsidian 输出从“事件目录”升级为“过滤结果 + 候选排序 + 首页工作台”。

**Architecture:** 先在导出链路前增加一个纯规则的 surface snapshot，统一产出过滤结果、候选队列、主题候选，再由 Obsidian exporter 把 snapshot 渲染成 dashboard、daily、event、topic 四类笔记。LLM 不参与过滤和排序，只保留在后续命名/解释层。

**Tech Stack:** Python 3.11+, SQLite, pytest, Obsidian markdown exporter

---

### Task 1: 构建过滤与候选排序快照

**Files:**
- Create: `keypulse/pipeline/surface.py`
- Test: `tests/test_pipeline_surface.py`

- [ ] **Step 1: 写失败测试，覆盖过滤、打分、聚合**

```python
from keypulse.pipeline.surface import build_surface_snapshot


def test_build_surface_snapshot_filters_noise_and_ranks_candidates():
    events = [
        {"source": "idle", "event_type": "idle_start", "ts_start": "2026-04-19T09:00:00+00:00"},
        {
            "source": "window",
            "event_type": "window_focus",
            "ts_start": "2026-04-19T09:01:00+00:00",
            "app_name": "终端",
            "window_title": "终端",
        },
        {
            "source": "manual",
            "event_type": "manual_save",
            "ts_start": "2026-04-19T09:02:00+00:00",
            "content_text": "修复 retention 启动崩溃，避免 VACUUM 在事务内执行。",
            "metadata_json": "{\"tags\":\"ops,sqlite\"}",
        },
        {
            "source": "clipboard",
            "event_type": "clipboard_copy",
            "ts_start": "2026-04-19T09:03:00+00:00",
            "content_text": "需要把失败后的 deterministic fallback 做成明确策略。",
            "metadata_json": "{\"tags\":\"sre,architecture\"}",
        },
    ]

    snapshot = build_surface_snapshot(events, top_k=5)

    assert snapshot["filtered_total"] == 2
    assert snapshot["filtered_reasons"]["idle_event"] == 1
    assert snapshot["filtered_reasons"]["low_signal_window"] == 1
    assert [item["title"] for item in snapshot["candidates"]] == [
        "修复 retention 启动崩溃，避免 VACUUM 在事务内执行。",
        "需要把失败后的 deterministic fallback 做成明确策略。",
    ]
    assert snapshot["theme_candidates"][0]["topic_key"] == "ops-sqlite"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd /Users/Harland/Go/keypulse && pytest -q tests/test_pipeline_surface.py`

Expected: `ModuleNotFoundError` 或导入失败。

- [ ] **Step 3: 实现最小 surface snapshot**

```python
# keypulse/pipeline/surface.py
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any


def build_surface_snapshot(events: list[dict[str, Any]], top_k: int = 10) -> dict[str, Any]:
    filtered_reasons: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []

    for event in events:
        reason = classify_filtered_reason(event)
        if reason:
            filtered_reasons[reason] += 1
            continue
        candidates.append(score_candidate(event))

    candidates.sort(key=lambda item: (-item["score"], item["created_at"], item["title"]))
    theme_candidates = aggregate_theme_candidates(candidates)

    return {
        "filtered_total": sum(filtered_reasons.values()),
        "filtered_reasons": dict(filtered_reasons),
        "candidates": candidates[:top_k],
        "theme_candidates": theme_candidates,
    }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd /Users/Harland/Go/keypulse && pytest -q tests/test_pipeline_surface.py`

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/Harland/Go/keypulse
git add keypulse/pipeline/surface.py tests/test_pipeline_surface.py
git commit -m "feat: add noise filter surface snapshot"
```

### Task 2: 把 snapshot 写进 Obsidian 工作台

**Files:**
- Modify: `keypulse/obsidian/exporter.py`
- Test: `tests/test_obsidian_exporter.py`

- [ ] **Step 1: 写失败测试，覆盖 dashboard 首页**

```python
from keypulse.obsidian.exporter import build_obsidian_bundle


def test_build_obsidian_bundle_creates_dashboard_surface():
    bundle = build_obsidian_bundle(
        [
            {
                "created_at": "2026-04-19T09:02:00+00:00",
                "source": "manual",
                "event_type": "manual_save",
                "content_text": "修复 retention 启动崩溃，避免 VACUUM 在事务内执行。",
                "metadata_json": "{\"tags\":\"ops,sqlite\"}",
            }
        ],
        vault_name="KeyPulse",
        date_str="2026-04-19",
    )

    dashboard = bundle["dashboard"][0]
    assert dashboard["path"] == "Dashboard/Today.md"
    assert "## Top Signals Today" in dashboard["body"]
    assert "## Filtered Out" in dashboard["body"]
    assert "## Theme Candidates" in dashboard["body"]
    assert "## Review Queue" in dashboard["body"]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd /Users/Harland/Go/keypulse && pytest -q tests/test_obsidian_exporter.py::test_build_obsidian_bundle_creates_dashboard_surface`

Expected: `KeyError: 'dashboard'` 或断言失败。

- [ ] **Step 3: 最小改造 exporter，新增 dashboard 输出**

```python
from keypulse.pipeline.surface import build_surface_snapshot


snapshot = build_surface_snapshot(items, top_k=10)
dashboard_card = _build_note_card(
    "dashboard",
    date_str,
    "dashboard",
    {"created_at": f"{date_str}T00:00:00+00:00"},
    dashboard_body,
    extra_props={"vault": vault_name, "candidate_count": len(snapshot["candidates"])},
    path="Dashboard/Today.md",
)
```

Dashboard body 至少包含四段：

```text
## Top Signals Today
## Filtered Out
## Theme Candidates
## Review Queue
```

Daily note 顶部补一个 dashboard 链接：

```text
- Dashboard: [[Dashboard/Today]]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd /Users/Harland/Go/keypulse && pytest -q tests/test_obsidian_exporter.py`

Expected: `all passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/Harland/Go/keypulse
git add keypulse/obsidian/exporter.py tests/test_obsidian_exporter.py
git commit -m "feat: add obsidian dashboard surface"
```

### Task 3: 端到端验证并清理导出细节

**Files:**
- Modify: `keypulse/obsidian/exporter.py`
- Optional Test: `tests/test_obsidian_exporter.py`

- [ ] **Step 1: 验证同日重复同步不会堆脏 dashboard/event 文件**

Run:

```bash
cd /Users/Harland/Go/keypulse
/Users/Harland/.keypulse/venv/bin/keypulse pipeline sync --date 2026-04-19
find /Users/Harland/Go/Knowledge/Events/2026-04-19 -maxdepth 1 -type f | sort
sed -n '1,200p' /Users/Harland/Go/Knowledge/Dashboard/Today.md
```

Expected:
- event 文件只保留当前策略生成的版本
- dashboard 文件存在
- dashboard 中出现过滤结果、候选排序、主题候选、处理动作

- [ ] **Step 2: 如发现重复文件或丢链接，补最小修复**

修复原则：
- 只清当前日期的 event 输出
- dashboard 始终覆盖单一固定路径
- 不新增额外索引层级

- [ ] **Step 3: 运行全量测试**

Run: `cd /Users/Harland/Go/keypulse && pytest -q`

Expected: `all passed`

- [ ] **Step 4: Commit**

```bash
cd /Users/Harland/Go/keypulse
git add keypulse/obsidian/exporter.py tests/test_obsidian_exporter.py
git commit -m "test: verify obsidian surface sync flow"
```

## Self-Review

- Spec coverage:
  - 过滤规则层：Task 1
  - 候选打分层：Task 1
  - Obsidian 首页展示层：Task 2
  - 重复同步与可用性验证：Task 3
- Placeholder scan:
  - 没有使用 TBD/TODO
  - 每个任务都有明确文件和命令
- Type consistency:
  - `build_surface_snapshot()` 作为统一入口
  - exporter 只消费 snapshot，不重复做过滤逻辑

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-keypulse-noise-filter-and-surface-implementation.md`. Execution mode is already chosen: **Subagent-Driven**. I will dispatch mini workers task-by-task and review between tasks.
