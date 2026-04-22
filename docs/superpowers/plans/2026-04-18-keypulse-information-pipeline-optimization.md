# KeyPulse Information Pipeline Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn KeyPulse into a six-layer information-processing pipeline that records cheaply, guarantees at least one model-assisted normalization pass on the critical path, mines high-value knowledge candidates, aggregates stable themes, surfaces the right context, and learns from user feedback without wasting LLM budget.

**Architecture:** Keep `Record`, `Surface`, and `Feedback` deterministic. Make `Write` the mandatory model-assisted normalization step whenever there is non-empty text to process, then allow additional LLM usage in `Mine` and `Aggregate` only when a policy planner says the quality gain is worth the cost. The first task introduces a shared pipeline contract, a pluggable model gateway, and a budget planner so later stages can use the same rules for backend selection, call limits, candidate limits, and fallback behavior.

**Tech Stack:** Python 3.13, Pydantic, Click, SQLite, pytest, `httpx` or `requests`, local file caches, markdown export, JSON-over-HTTP model adapters.

---

### Task 1: Add pipeline contracts, a model gateway, and an LLM budget planner

**Files:**
- Create: `keypulse/pipeline/__init__.py`
- Create: `keypulse/pipeline/contracts.py`
- Create: `keypulse/pipeline/model.py`
- Create: `keypulse/pipeline/backends.py`
- Create: `keypulse/pipeline/policy.py`
- Modify: `keypulse/config.py`
- Modify: `config.toml`
- Create: `tests/test_pipeline_policy.py`
- Create: `tests/test_pipeline_model.py`

The pipeline config must grow a small model-backend section with ordered backend entries so the gateway can try local backends first, then fall back to cloud or domestic providers when needed. Suggested fields:

- `enabled_backends`
- `backend_priority`
- `lm_studio_base_url`
- `ollama_base_url`
- `openai_api_key`
- `anthropic_api_key`
- `openai_compatible_base_url`
- `custom_http_endpoints`

- [ ] **Step 1: Write the failing test**

```python
from keypulse.pipeline.contracts import PipelineInputs, PipelineStage
from keypulse.pipeline.model import ModelBackend, ModelGateway
from keypulse.pipeline.policy import build_pipeline_plan, LLMMode


def test_off_mode_keeps_only_the_mandatory_write_call():
    plan = build_pipeline_plan(
        LLMMode.OFF,
        PipelineInputs(event_count=80, candidate_count=20, topic_count=12, active_days=7),
    )

    assert plan.write.use_llm is True
    assert plan.write.mandatory_model_call is True
    assert plan.mine.use_llm is False
    assert plan.aggregate.use_llm is False


def test_balanced_mode_spends_llm_on_mine_before_write():
    plan = build_pipeline_plan(
        LLMMode.BALANCED,
        PipelineInputs(event_count=12, candidate_count=9, topic_count=4, active_days=2),
    )

    assert plan.write.use_llm is False
    assert plan.mine.use_llm is True
    assert plan.mine.max_items == 9
    assert plan.aggregate.use_llm is False


def test_high_mode_allows_weekly_aggregation():
    plan = build_pipeline_plan(
        LLMMode.HIGH,
        PipelineInputs(event_count=60, candidate_count=18, topic_count=14, active_days=9),
    )

    assert plan.write.use_llm is True
    assert plan.mine.use_llm is True
    assert plan.aggregate.use_llm is True


def test_write_stage_requires_one_model_normalization_when_text_exists():
    plan = build_pipeline_plan(
        LLMMode.OFF,
        PipelineInputs(event_count=1, candidate_count=0, topic_count=0, active_days=1),
    )

    assert plan.write.mandatory_model_call is True


def test_model_gateway_prefers_local_backend_when_available():
    gateway = ModelGateway(
        backends=[
            ModelBackend(kind="lm_studio", base_url="http://localhost:1234", model="marco-mini-instruct"),
            ModelBackend(kind="openai_compatible", base_url="https://api.example.com", model="gpt-4.1-mini"),
        ]
    )

    selected = gateway.select_backend()

    assert selected.kind == "lm_studio"
    assert selected.model == "marco-mini-instruct"
```

```python
# tests/test_pipeline_model.py
from keypulse.pipeline.model import ModelBackend, ModelGateway


def test_model_gateway_falls_back_to_cloud_when_local_backends_fail():
    gateway = ModelGateway(
        backends=[
            ModelBackend(kind="lm_studio", base_url="http://127.0.0.1:65535", model="marco-mini-instruct"),
            ModelBackend(kind="openai_compatible", base_url="https://api.example.com", model="gpt-4.1-mini"),
        ]
    )

    selected = gateway.select_backend(healthcheck=lambda backend: backend.kind != "lm_studio")

    assert selected.kind == "openai_compatible"
    assert selected.model == "gpt-4.1-mini"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_policy.py -v`
Expected: FAIL because `keypulse.pipeline` and the model gateway do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class LLMMode(StrEnum):
    OFF = "off"
    BALANCED = "balanced"
    HIGH = "high"


class PipelineStage(StrEnum):
    RECORD = "record"
    WRITE = "write"
    MINE = "mine"
    AGGREGATE = "aggregate"
    SURFACE = "surface"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class PipelineInputs:
    event_count: int
    candidate_count: int
    topic_count: int
    active_days: int
    llm_calls_used: int = 0
    llm_input_chars_used: int = 0


@dataclass(frozen=True)
class StageBudget:
    stage: PipelineStage
    use_llm: bool
    mandatory_model_call: bool
    max_items: int
    reason: str


@dataclass(frozen=True)
class PipelinePlan:
    write: StageBudget
    mine: StageBudget
    aggregate: StageBudget


@dataclass(frozen=True)
class ModelBackend:
    kind: str
    base_url: str
    model: str


@dataclass(frozen=True)
class ModelGateway:
    backends: list[ModelBackend]

    def select_backend(self, healthcheck=None) -> ModelBackend:
        candidates = [backend for backend in self.backends if healthcheck is None or healthcheck(backend)]
        for backend in candidates:
            if backend.kind == "lm_studio":
                return backend
        return candidates[0]


def build_pipeline_plan(mode: LLMMode, inputs: PipelineInputs) -> PipelinePlan:
    off = mode == LLMMode.OFF
    balanced = mode == LLMMode.BALANCED
    high = mode == LLMMode.HIGH

    write_use_llm = inputs.event_count > 0
    mine_use_llm = (balanced or high) and not off and inputs.candidate_count > 0
    aggregate_use_llm = high and not off and inputs.topic_count >= 8 and inputs.active_days >= 7

    return PipelinePlan(
        write=StageBudget(PipelineStage.WRITE, write_use_llm, inputs.event_count > 0, 1 if write_use_llm else 0, "daily draft must normalize any non-empty text bundle through the model"),
        mine=StageBudget(PipelineStage.MINE, mine_use_llm, False, min(inputs.candidate_count, 12) if mine_use_llm else 0, "rank only the best candidates"),
        aggregate=StageBudget(PipelineStage.AGGREGATE, aggregate_use_llm, False, min(inputs.topic_count, 8) if aggregate_use_llm else 0, "weekly theme consolidation"),
    )
```

For the real implementation, `ModelGateway` must also support:

- `lm_studio` for local `http://localhost:1234` or user-provided ports
- `ollama` for local Ollama servers
- `openai_compatible` for providers that expose OpenAI-style chat endpoints
- `openai` and `anthropic` native adapters when the provider SDK is preferred
- `custom_http` for domestic providers with a slightly different request envelope

The gateway should pick the first healthy backend and record the chosen backend in state so a stable local backend is reused until it fails.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/pipeline tests/test_pipeline_policy.py keypulse/config.py config.toml
git commit -m "feat: add pipeline budget planner"
```

### Task 2: Build record normalization and a model-assisted daily draft

**Files:**
- Create: `keypulse/pipeline/record.py`
- Create: `keypulse/pipeline/write.py`
- Create: `tests/test_pipeline_write.py`

- [ ] **Step 1: Write the failing test**

```python
from keypulse.pipeline.contracts import PipelineInputs
from keypulse.pipeline.write import build_daily_draft


def test_build_daily_draft_uses_the_model_for_standard_markdown():
    draft = build_daily_draft(
        PipelineInputs(event_count=3, candidate_count=1, topic_count=0, active_days=1),
        events=[
            {"title": "Fix install path", "body": "install.sh now reuses the venv"},
            {"title": "Add sink detect", "body": "autobind to the best local vault"},
        ],
        model_gateway=type(
            "Gateway",
            (),
            {
                "model_name": "marco-mini-instruct",
                "prompt_hash": "draft-v1",
                "normalize_markdown": lambda self, text: "```markdown\n# Daily Draft\n\n- Fix install path: install.sh now reuses the venv\n- Add sink detect: autobind to the best local vault\n```",
            },
        )(),
    )

    assert "Fix install path" in draft.body
    assert "Add sink detect" in draft.body
    assert draft.llm_used is True
    assert draft.model_name == "marco-mini-instruct"
    assert draft.prompt_hash == "draft-v1"
    assert draft.body.startswith("```markdown")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_write.py -v`
Expected: FAIL because the write layer does not yet call a model gateway and return standardized markdown.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class DailyDraft:
    body: str
    llm_used: bool = False
    model_name: str = ""
    prompt_hash: str = ""


def build_daily_draft(inputs, events, model_gateway=None):
    lines = ["# Daily Draft", ""]
    for event in events:
        lines.append(f"- {event['title']}: {event.get('body', '')}")
    draft = "\n".join(lines)
    normalized = model_gateway.normalize_markdown(draft) if model_gateway else draft
    return DailyDraft(
        body=normalized,
        llm_used=bool(model_gateway),
        model_name=getattr(model_gateway, "model_name", ""),
        prompt_hash=getattr(model_gateway, "prompt_hash", ""),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_write.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/pipeline/record.py keypulse/pipeline/write.py tests/test_pipeline_write.py
git commit -m "feat: add model-assisted daily draft writer"
```

### Task 3: Add candidate mining with LLM gating, cache keys, and model-backed ranking

**Files:**
- Create: `keypulse/pipeline/mine.py`
- Create: `keypulse/pipeline/cache.py`
- Create: `tests/test_pipeline_mine.py`

The mining step should rank candidates deterministically first, then send only the top-ranked, evidence-rich subset to the model gateway when the budget planner says there is headroom.

- [ ] **Step 1: Write the failing test**

```python
from keypulse.pipeline.contracts import PipelineInputs
from keypulse.pipeline.mine import select_mining_candidates


def test_select_mining_candidates_skips_low_value_items_when_budget_tight():
    candidates = select_mining_candidates(
        PipelineInputs(event_count=50, candidate_count=6, topic_count=2, active_days=1),
        items=[
            {"title": "Open terminal", "score": 0.1},
            {"title": "Write design note", "score": 0.9},
            {"title": "Copy snippet", "score": 0.8},
        ],
        llm_budget_remaining=1,
    )

    assert [item["title"] for item in candidates] == ["Write design note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_mine.py -v`
Expected: FAIL because the mining layer does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def select_mining_candidates(inputs, items, llm_budget_remaining):
    ranked = sorted(items, key=lambda item: item.get("score", 0), reverse=True)
    if llm_budget_remaining <= 0:
        return []
    return ranked[:1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_mine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/pipeline/mine.py keypulse/pipeline/cache.py tests/test_pipeline_mine.py
git commit -m "feat: add gated mining candidate selection"
```

### Task 4: Add aggregation, surface, and feedback wiring with model-assisted theme summaries

**Files:**
- Create: `keypulse/pipeline/aggregate.py`
- Create: `keypulse/pipeline/surface.py`
- Create: `keypulse/pipeline/feedback.py`
- Modify: `keypulse/cli.py`
- Modify: `README.md`
- Create: `tests/test_pipeline_aggregate.py`

Aggregation should first cluster facts and repeated themes locally, then hand the compact theme packet to the model gateway for standardized theme wording, counterpoint cleanup, and concise summaries when budget allows.

- [ ] **Step 1: Write the failing test**

```python
from keypulse.pipeline.aggregate import build_theme_summary


def test_build_theme_summary_groups_repeated_topics():
    summary = build_theme_summary([
        {"topic": "decision-making", "title": "Better selection rules"},
        {"topic": "decision-making", "title": "Daily budget policy"},
        {"topic": "method", "title": "Deterministic fallback"},
    ])

    assert "decision-making" in summary.body
    assert summary.llm_used is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_aggregate.py -v`
Expected: FAIL because the aggregation layer does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeSummary:
    body: str
    llm_used: bool = False


def build_theme_summary(items):
    lines = ["# Theme Summary", ""]
    for item in items:
        lines.append(f"- {item['topic']}: {item['title']}")
    return ThemeSummary(body="\n".join(lines), llm_used=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_aggregate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/pipeline aggregate surface feedback keypulse/cli.py README.md tests/test_pipeline_aggregate.py
git commit -m "feat: add theme aggregation surface"
```

### Task 5: Verify the end-to-end chain and tune the defaults

**Files:**
- Verify: `keypulse/pipeline/*`
- Verify: `keypulse/cli.py`
- Verify: `config.toml`
- Verify: `README.md`

- [ ] **Step 1: Run the focused test suite**

Run: `pytest tests/test_pipeline_policy.py tests/test_pipeline_write.py tests/test_pipeline_mine.py tests/test_pipeline_aggregate.py -q`
Expected: all tests pass.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Exercise the low-cost defaults**

Run: `python3 -m keypulse.cli config show --plain`
Expected: pipeline defaults are visible, with `llm_mode=off` or another explicit safe default until the LLM adapter is wired.

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "feat: optimize keypulse information pipeline"
```
