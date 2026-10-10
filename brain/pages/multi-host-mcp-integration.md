---
id: multi-host-mcp-integration
title: "All major local AI IDE hosts (Claude, Codex, Cursor) integrate in V1 via one MCP server and thin per-host adapters"
category: decision
status: active
tags: [mcp, hosts, v1]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T11:19:06"
---

<!-- compiled_truth -->
## Decision
- V1 must integrate **all major local AI IDE hosts**: Claude, Codex, Cursor.
- Implementation: **one MCP server** in core, plus **thin per-host adapters**.

## Alternatives
- Ship one host first (the assistant's suggestion). Rejected by the owner.

## Consequence
- Host-specific logic stays in adapters; core exposes only the MCP tool surface.
- Related: [[keypulse-v1-local-memory-infra]], [[mcp-caller-auth-p0]], [[memory-distillation-by-agent]].


## Timeline

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Created this page: All major local AI IDE hosts (Claude, Codex, Cursor) integrate in V1 via one MCP server and thin per-host adapters"
  source: "Owner decision 2026-10-10 (D-f)"
  affects: [multi-host-mcp-integration]

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Owner decision D-f: all three hosts in V1 via one MCP server plus thin adapters"
  source: Owner decision 2026-10-10
  affects: [multi-host-mcp-integration]
