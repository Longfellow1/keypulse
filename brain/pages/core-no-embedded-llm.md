---
id: core-no-embedded-llm
title: "Core layer does not embed an LLM; the consuming agent extracts and summarises"
category: decision
status: active
tags: [core, llm, privacy]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T11:19:16"
---

<!-- compiled_truth -->
## Decision
The KeyPulse core layer does **not** call an LLM. Memory extraction and summarisation are done by the consuming agent (the local AI IDE / MCP client). Core only stores, dedupes, merges by rules, and retrieves.
- The in-core "Distill Worker" is **rule-based**. It is not an LLM path.
- See [[memory-distillation-by-agent]].

## Alternatives
- Core embeds a model gateway and runs the pipeline (current legacy design, `pipeline/model.py` + ModelGateway): rejected for V1.

## Rationale
- Removes the pipeline / ModelGateway coupling from the core.
- Simpler privacy story: no data leaves the machine through core.

## Consequence
- Legacy `pipeline/` LLM paths are not ported into core. Extraction blockers are listed in [[legacy-extraction-blockers]].
- Related: [[keypulse-v1-local-memory-infra]], [[skill-layer]], [[multi-host-mcp-integration]].


## Timeline

- time: 2026-10-10T10:51:08
  kind: decision
  summary: "Created this page: Core layer does not embed an LLM; the consuming agent extracts and summarises"
  source: Owner decision 2026-10-10
  affects: [core-no-embedded-llm]

- time: 2026-10-10T10:51:08
  kind: decision
  summary: Initial decision recorded
  source: Owner decision 2026-10-10
  affects: [core-no-embedded-llm]

- time: 2026-10-10T11:19:16
  kind: decision
  summary: "Add owner decision D-b: Distill Worker is rule-based, no LLM in core"
  source: Owner decision 2026-10-10
  affects: [core-no-embedded-llm]
