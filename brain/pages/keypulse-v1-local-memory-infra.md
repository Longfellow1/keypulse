---
id: keypulse-v1-local-memory-infra
title: "KeyPulse becomes local memory and context infrastructure, MCP-first, for local AI IDEs"
category: decision
status: active
tags: [product, v1, mcp]
created: "2026-10-10T10:51:09"
updated: "2026-10-10T11:19:16"
---

<!-- compiled_truth -->
## Decision
KeyPulse becomes **local infrastructure: memory + context, MCP-first**, for local AI IDEs.
- Authoritative baseline: `docs/KeyPulse_V2.md` (titled "V1 PRD", becoming v1.2). It supersedes `docs/KeyPulse_V1_PRD.md`. See [[v1-development-baseline-prd]].
- Concept narrative: `docs/Keypulse-new.html`.
- V1 demotes daily / weekly / Obsidian and puts skill generation at P2 (see [[skill-layer]], [[knowledge-graph-layer]]).

## Owner decisions (2026-10-10)
- Memory distillation is the agent's job. Core stores, dedupes, merges by rules, retrieves. `commit_memory` is P0. See [[memory-distillation-by-agent]].
- P0 MCP caller auth: local token plus client allowlist (per-Host AccessGrant). See [[mcp-caller-auth-p0]].
- Keyboard capture stays P0, with redaction as P1 hardening. See [[keyboard-capture-p0-experimental]].
- Success criteria and eval live in a separate Eval PRD. Baseline PRD metrics are non-binding candidates until then. See [[eval-prd-separate]].
- All major hosts (Claude, Codex, Cursor) integrate in V1 via one MCP server plus thin per-host adapters. See [[multi-host-mcp-integration]].

## Open (not decided)
- Eval PRD (success criteria, acceptance) not yet written; the owner will provide or commission it.
- Windows, personal-chat capture details, camera capture. See [[v1-deferred-undecided-items]].
- The baseline PRD is pending its v1.2 revision; items marked 审阅修订 still need owner approve or veto.

## Consequence
- Core stores and retrieves; the agent extracts ([[core-no-embedded-llm]]).
- Refactor is in place ([[refactor-in-place-with-legacy-tag]]); legacy data handled per [[legacy-data-new-schema-importer]].


## Timeline

- time: 2026-10-10T10:51:09
  kind: decision
  summary: "Created this page: KeyPulse becomes local memory and context infrastructure, MCP-first, for local AI IDEs"
  source: "docs/KeyPulse_V1_PRD.md (authoritative); docs/Keypulse-new.html (concept narrative); owner decision 2026-10-10"
  affects: [keypulse-v1-local-memory-infra]

- time: 2026-10-10T10:51:09
  kind: decision
  summary: "Initial decision recorded; V1 scope and first target IDE left open"
  source: "docs/KeyPulse_V1_PRD.md; docs/Keypulse-new.html; owner decision 2026-10-10"
  affects: [keypulse-v1-local-memory-infra]

- time: 2026-10-10T11:19:16
  kind: decision
  summary: "Record owner decisions D-b..D-f: distillation by agent, P0 auth, baseline PRD v1.2, separate Eval PRD, all three hosts"
  source: Owner decision 2026-10-10
  affects: [keypulse-v1-local-memory-infra]
