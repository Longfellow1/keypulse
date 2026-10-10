---
id: user-curates-llm-proposes
title: "User names and curates; LLM only proposes; code writes core state"
category: decision
status: active
created: "2026-10-10T10:26:12"
updated: "2026-10-10T10:26:12"
---

<!-- compiled_truth -->
## Decision
For skills and principles: the user names and curates. The LLM may only propose candidates. Deterministic program code writes core state (anchors, skill registry, exports).

## Related rule
Generic first: no per-app special-casing. Test: "installing a new app should need zero code" (2026-05-15).

## Status
Applies to the planned skill layer, which is not implemented yet. Current code has only `keypulse skill propose`, which is a placeholder.

## Open
The exact approval UX is not decided in code. A candidate-approval HUD exists for sources (N4), which is a possible reuse. Confirm before building.


## Timeline

- time: 2026-10-10T10:26:12
  kind: decision
  summary: "Created this page: User names and curates; LLM only proposes; code writes core state"
  source: "project principle from the brief; docs/设计-skill-v0-plan.md; EVOLUTION 2026-05-15 'zero-code new app' rule"
  affects: [user-curates-llm-proposes]

- time: 2026-10-10T10:26:12
  kind: decision
  summary: captured from project brief
  source: "project principle from the brief; docs/设计-skill-v0-plan.md; EVOLUTION 2026-05-15 'zero-code new app' rule"
  affects: [user-curates-llm-proposes]
