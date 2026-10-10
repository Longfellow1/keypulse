---
id: v1-development-baseline-prd
title: "docs/KeyPulse_V2.md (V1 PRD) is the formal development baseline, superseding KeyPulse_V1_PRD.md"
category: decision
status: active
tags: [prd, v1, baseline]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T11:56:21"
---

<!-- compiled_truth -->
## Decision
- `docs/KeyPulse_V2.md` (titled "V1 PRD") is the **formal development baseline**. After its pending revision it becomes **v1.2**.
- It supersedes `docs/KeyPulse_V1_PRD.md`.
- **Filename is NOT renamed for now.** Content is the V1 PRD v1.2 baseline and the header states it. Rename later if desired; update brain references then.
- PRD v1.2 with D18 (Windows) and D19 (chat capture) is committed (8a955c0); the Eval PRD v0.1 draft is committed alongside it.

## PRD v1.2 review revisions (B1-B8): APPROVED
- B1: `coverage.note` must not reveal Private content.
- B2: examples must not depend on Feishu. Feishu / WeChat are the V1.1 pilot.
- B3: macOS-only, unchanged.
- B4: agent-written Memory defaults to `declared`. It cannot cite unreadable Evidence. It is bucketed within the Grant.
- B5: the keyboard-capture toggle UI must disclose best-effort filtering and possible cloud egress.
- B6: the user's App/domain exclusion list is NOT "desensitization". It must work (AT18). KeyPulse's own token is never written to logs or audit.
- B7: `get_memory` stays P1. Memory hits are returned via `search_context` in P0.
- B8: AT12 is up to P0. Add AT15-AT19 (AT20 added later for personal chat, see D19). AT07 is non-blocking.

## Conflict resolutions
1. `get_memory` stays P1 (B7 workaround accepted).
2. "No passwords in logs" is best-effort. KeyPulse's own tokens and credentials are a hard ban.
3. "Unauthorised-access leakage = 0" stays a **HARD release gate**. It is a security regression check, not an effectiveness metric. The Eval PRD must not relax it.
4. Cursor: P0 as an MCP host. Parser-as-data-source stays P1.
5. Filename: see Decision above.

## Resolutions carried by this baseline
- Platform: **macOS only** for V1. Windows is resolved as D18: core stays platform-neutral (import-linter in CI), platform code only in adapters; re-evaluate after V1.0 if Windows demand >= 30% of seed users (see [[v1-deferred-undecided-items]]).
- Personal chat capture resolved as D19: chat apps on the default exclusion list; per-app explicit opt-in; opted-in AX content defaults to Private; no local chat-DB decryption; Feishu / Slack official OAuth = V1.1 pilot.
- Privacy buckets: defaults per PRD §3.4.2. Work and Personal are open to authorised agents. Private and Financial are denied by default.
- Feishu / WeChat: **V1.1 pilot**. No special casing in the data model.

## Still open
- Camera capture: not yet specified (owner has not decided).
- Cursor data-source format: not specified in the PRD. To confirm.
- Known limitation: W01 has no work-chat history in V1.

## Consequence
- Legacy design choices that conflict with v1.2 are not carried forward. See [[refactor-in-place-with-legacy-tag]].
- Related: [[keypulse-v1-local-memory-infra]], [[eval-prd-separate]], [[mcp-caller-auth-p0]], [[keyboard-capture-p0-experimental]].


## Timeline

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Created this page: docs/KeyPulse_V2.md (V1 PRD) is the formal development baseline, superseding KeyPulse_V1_PRD.md"
  source: "Owner decision 2026-10-10 (D-d)"
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Owner decision D-d: docs/KeyPulse_V2.md is the formal baseline (to become v1.2), supersedes docs/KeyPulse_V1_PRD.md"
  source: Owner decision 2026-10-10
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:30:16
  kind: decision
  summary: "Record owner-approved PRD v1.2 review (B1-B8), resolved conflicts, and remaining open items"
  source: "Owner delegated to assistant recommendation, 2026-10-10"
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:56:21
  kind: decision
  summary: "Still-open section: Windows and personal-chat capture resolved (D18/D19)"
  source: Owner decision 2026-10-10
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:56:21
  kind: decision
  summary: "Still-open list updated: Windows (D18) and personal-chat capture (D19, AT20) resolved; camera capture and Cursor data-source format remain open."
  source: Owner decision 2026-10-10
  affects: [v1-development-baseline-prd, v1-deferred-undecided-items]
