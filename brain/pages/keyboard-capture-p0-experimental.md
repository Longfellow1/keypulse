---
id: keyboard-capture-p0-experimental
title: "Keyboard capture stays P0 (opt-in, filtered text chunks) during the experimental phase"
category: decision
status: active
tags: [scope, capture, privacy]
created: "2026-10-10T11:19:05"
updated: "2026-10-10T11:19:05"
---

<!-- compiled_truth -->
## Decision
Keyboard capture remains **P0**: opt-in, filtered text chunks only.
- Experimental phase: result quality (retrieval effectiveness) comes first.
- Data desensitisation / redaction is **not** a P0 gate. It is best-effort and moves to P1 hardening.

## Alternatives
- Demote keyboard capture to P1 (the assistant's recommendation). Rejected by the owner: priority is result quality in the experimental phase.

## Consequence
- Privacy posture for P0 relies on opt-in and filtering, not on a redaction pass. Revisit before any non-experimental release.
- Related: [[v1-deferred-undecided-items]], [[keypulse-v1-local-memory-infra]].


## Timeline

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Created this page: Keyboard capture stays P0 (opt-in, filtered text chunks) during the experimental phase"
  source: "Owner decision 2026-10-10 (D-a)"
  affects: [keyboard-capture-p0-experimental]

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Owner decision D-a: keyboard capture kept at P0; redaction is best-effort P1 hardening"
  source: Owner decision 2026-10-10
  affects: [keyboard-capture-p0-experimental]
