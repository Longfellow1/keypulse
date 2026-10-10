---
id: v1-deferred-undecided-items
title: "Deferred or undecided for V1: cloud privacy defaults, Windows, chat capture, keyboard/camera capture"
category: decision
status: active
tags: [scope, open-questions]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T11:56:15"
---

<!-- compiled_truth -->
## Status: partly resolved (2026-10-10). Items below are not to be assumed beyond what is stated.

- **Privacy bucket defaults for cloud AI via MCP: RESOLVED.** Per [[v1-development-baseline-prd]] (PRD §3.4.2): Work and Personal open to authorised agents; Private and Financial denied by default.
- **Windows support: RESOLVED (D18).** V1 is macOS only. The core package must not import macOS-only libraries; platform code lives only in the capture / HUD / startup-install adapters, enforced by import-linter in CI. Re-evaluate after V1.0 passes the Eval PRD, and only if seed users with explicit Windows demand reach >= 30%. First step would be a "Windows lite" (structured sources + MCP, no dynamic capture / HUD).
- **Personal chat tool capture: RESOLVED (D19).** Chat apps are on the default exclusion list; each app requires explicit per-app opt-in; on-screen (AX) content from opted-in chat apps defaults to the Private bucket. Local chat-database decryption is never built (ToS risk, fragile, contradicts the trust positioning). Feishu / Slack official OAuth APIs are the V1.1 pilot. Known limitation: W01 has no work-chat history in V1. Acceptance criterion AT20 added to the PRD.
- **Keyboard capture in V1: RESOLVED.** Stays P0, opt-in, filtered text chunks; redaction is P1 hardening. See [[keyboard-capture-p0-experimental]].
- **Camera capture in V1: OPEN.** Not decided by the owner on 2026-10-10. Do not assume a default.

## Consequence
- V1 scope and acceptance criteria: success criteria live in a separate Eval PRD, see [[eval-prd-separate]]. Until then they are non-binding.
- Do not design V1 around the OPEN items until the owner decides.
- Related: [[keypulse-v1-local-memory-infra]], [[no-real-data-in-repo]] (chat-derived eval data stays local).


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

- time: 2026-10-10T11:56:15
  kind: decision
  summary: "Windows and personal-chat capture resolved (D18/D19); camera capture still open"
  source: Owner decision 2026-10-10
  affects: [v1-deferred-undecided-items]

- time: 2026-10-10T11:56:15
  kind: decision
  summary: "Owner decisions: Windows resolved as V1 macOS-only (D18, core import-linter rule, Windows re-evaluation gate >=30% demand after V1.0); personal chat capture resolved (D19, default exclusion, per-app opt-in, AX content to Private, no chat-DB decryption, V1.1 official OAuth pilot, AT20). Camera capture remains OPEN."
  source: Owner decision 2026-10-10
  affects: [v1-deferred-undecided-items, v1-development-baseline-prd]
