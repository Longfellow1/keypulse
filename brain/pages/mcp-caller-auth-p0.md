---
id: mcp-caller-auth-p0
title: "MCP caller auth at P0: local token plus client allowlist (per-Host AccessGrant)"
category: decision
status: active
tags: [mcp, security, auth]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T11:19:06"
---

<!-- compiled_truth -->
## Decision
P0 MCP caller authentication is the minimal scheme:
- A **local token** that the MCP server issues and checks on each call.
- A **client allowlist**, modelled as a per-Host `AccessGrant`. Only listed hosts/clients may call.

## Consequence
- Stronger caller auth (per-tool scopes, signed grants, rotation) is out of P0 scope unless the owner says otherwise.
- Interacts with the privacy buckets in [[v1-development-baseline-prd]] (authorised agents only for Work/Personal).
- Related: [[keypulse-v1-local-memory-infra]], [[multi-host-mcp-integration]].


## Timeline

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Created this page: MCP caller auth at P0: local token plus client allowlist (per-Host AccessGrant)"
  source: "Owner decision 2026-10-10 (D-c)"
  affects: [mcp-caller-auth-p0]

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Owner decision D-c: minimal P0 auth = local token + client allowlist"
  source: Owner decision 2026-10-10
  affects: [mcp-caller-auth-p0]
