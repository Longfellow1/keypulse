---
id: eval-prd-separate
title: "Success criteria and eval live in a separate Eval PRD; PRD metrics are non-binding until then"
category: decision
status: active
tags: [eval, prd]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T12:14:18"
---

<!-- compiled_truth -->
## Decision
- Success criteria and evaluation are defined in a **separate Eval PRD**: `docs/KeyPulse_Eval_PRD.md`, **v1.0 / release-judgement baseline**, approved by the owner on 2026-10-10 and **binding**.
- The six core decisions (one line each):
  - Release gates: safety gates G1–G5 are not waivable. Of the 5 effect metrics, at most 1 may be waived in writing, with a stated remediation version.
  - End-to-end gate: M3 end-to-end effective recall ≥70% is the V1.0 release gate.
  - Pass lines: start with the Chapter 5 initial values; the owner confirms once after the first measured run; afterwards they only go up, never down.
  - Real data: owner's local data only; labels and details live in `~/.keypulse/eval/`; only aggregate numbers are committed; no eval artifact generated from real data may be committed (see brain `no-real-data-in-repo`).
  - Leakage test: canary scan + differential comparison.
  - Eval set size: 80 synthetic + 45 real questions (structure in PRD §3.3.2); 20% of real questions kept private; ≥10 new real questions per version.
- Baseline PRD metrics (§5.3 / §6.2 / §6.4) are now governed by this Eval PRD for success judgement. They stay non-binding candidates in the baseline PRD itself; the hard "unauthorised leakage = 0" gate remains binding.

## Consequence
- Acceptance criteria for V1 are now defined by this Eval PRD ([[keypulse-v1-local-memory-infra]]).
- Related: [[v1-development-baseline-prd]], [[session-handoff-2026-10-10]].


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

- time: 2026-10-10T12:14:18
  kind: decision
  summary: "Eval PRD v1.0 approved 2026-10-10; binding release gate"
  source: "Owner approval 2026-10-10, docs/KeyPulse_Eval_PRD.md v1.0"
  affects: [eval-prd-separate]
