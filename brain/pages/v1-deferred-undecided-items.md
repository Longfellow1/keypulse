---
id: v1-deferred-undecided-items
title: "Deferred or undecided for V1: cloud privacy defaults, Windows, chat capture, keyboard/camera capture"
category: decision
status: active
tags: [scope, open-questions]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T11:19:16"
---

<!-- compiled_truth -->
## Status: partly resolved (2026-10-10). Items below are not to be assumed beyond what is stated.

- **Privacy bucket defaults for cloud AI via MCP: RESOLVED.** Per [[v1-development-baseline-prd]] (PRD §3.4.2): Work and Personal open to authorised agents; Private and Financial denied by default.
- **Windows support: OPEN, deferred.** V1 is macOS only. Windows is a later decision.
- **Personal chat tool capture (WeChat / Feishu): PARTLY RESOLVED.** Feishu and WeChat are a V1.1 pilot with no special data-model casing. Capture details for personal chat are still OPEN.
- **Keyboard capture in V1: RESOLVED.** Stays P0, opt-in, filtered text chunks; redaction is P1 hardening. See [[keyboard-capture-p0-experimental]].
- **Camera capture in V1: OPEN.** Not decided by the owner on 2026-10-10. Do not assume a default.

## Consequence
- V1 scope and acceptance criteria: success criteria live in a separate Eval PRD, see [[eval-prd-separate]]. Until then they are non-binding.
- Do not design V1 around the OPEN items until the owner decides.
- Related: [[keypulse-v1-local-memory-infra]].


## Timeline

- time: 2026-10-10T10:51:08
  kind: decision
  summary: "Created this page: Deferred or undecided for V1: cloud privacy defaults, Windows, chat capture, keyboard/camera capture"
  source: Owner decision 2026-10-10
  affects: [v1-deferred-undecided-items]

- time: 2026-10-10T10:51:09
  kind: decision
  summary: "Initial decision recorded: items explicitly NOT decided"
  source: Owner decision 2026-10-10
  affects: [v1-deferred-undecided-items]

- time: 2026-10-10T11:19:16
  kind: decision
  summary: "Owner decisions 2026-10-10 resolve most deferred items; Windows, personal-chat details, camera capture remain open"
  source: "Owner decision 2026-10-10 (D-a, D-d)"
  affects: [v1-deferred-undecided-items]
