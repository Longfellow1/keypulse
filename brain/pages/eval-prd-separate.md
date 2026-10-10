---
id: eval-prd-separate
title: "Success criteria and eval live in a separate Eval PRD; PRD metrics are non-binding until then"
category: decision
status: active
tags: [eval, prd]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T11:43:20"
---

<!-- compiled_truth -->
## Decision
- Success criteria and evaluation are defined in a **separate Eval PRD**, which does not exist yet. The owner will provide or commission it.
- Until then, metrics in the baseline PRD are **non-binding candidates**. Do not gate work on them.

## Consequence
- Acceptance criteria for V1 remain open ([[keypulse-v1-local-memory-infra]]).
- Related: [[v1-development-baseline-prd]].


## Timeline

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Created this page: Success criteria and eval live in a separate Eval PRD; PRD metrics are non-binding until then"
  source: "Owner decision 2026-10-10 (D-e)"
  affects: [eval-prd-separate]

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Owner decision D-e: success criteria/eval move to a separate Eval PRD (not yet written)"
  source: Owner decision 2026-10-10
  affects: [eval-prd-separate]

- time: 2026-10-10T11:43:20
  kind: note
  summary: "Eval PRD draft exists at docs/KeyPulse_Eval_PRD.md (v0.1), pending owner decisions Q1 (real-data labels local-only), Q2 (initial thresholds, ratchet-up only), Q3 (E2E recall >=70% as V1.0 gate)"
  source: "Session 2026-10-10 (afternoon)"
  affects: [eval-prd-separate]
