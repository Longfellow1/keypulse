---
id: memory-distillation-by-agent
title: "Memory distillation is the AI IDE agent's job; core only stores, dedupes, merges and retrieves"
category: decision
status: active
tags: [core, memory, mcp]
created: "2026-10-10T11:19:05"
updated: "2026-10-10T11:19:05"
---

<!-- compiled_truth -->
## Decision
- Memory extraction and distillation are performed by the AI IDE agent (the MCP client), not by KeyPulse core.
- KeyPulse core only stores, dedupes and merges by rules, and retrieves.
- `commit_memory` moves to **P0** (the write path agents use to persist distilled memory).
- The "Distill Worker" inside core is **rule-based**. No LLM runs in core.

## Rationale
- Consistent with [[core-no-embedded-llm]]: no model call inside core, no pipeline/ModelGateway coupling.

## Consequence
- Core P0 surface: search, timeline, evidence retrieval, `commit_memory`, rule-based dedupe/merge. See [[keypulse-v1-local-memory-infra]].
- Related: [[core-no-embedded-llm]], [[legacy-extraction-blockers]].


## Timeline

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Created this page: Memory distillation is the AI IDE agent's job; core only stores, dedupes, merges and retrieves"
  source: "Owner decision 2026-10-10 (D-b)"
  affects: [memory-distillation-by-agent]

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Owner decision D-b: distillation moves to the agent; commit_memory is P0; Distill Worker is rule-based"
  source: Owner decision 2026-10-10
  affects: [memory-distillation-by-agent]
