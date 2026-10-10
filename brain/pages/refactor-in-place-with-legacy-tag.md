---
id: refactor-in-place-with-legacy-tag
title: "Refactor in place in the original repo; legacy-v0 tag is the frozen baseline"
category: decision
status: active
tags: [refactor, repo, legacy]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T13:53:19"
---

<!-- compiled_truth -->
## Decision
- The KeyPulse refactor happens **in place** in the original repository (github.com/Longfellow1/keypulse). No new repository.
- **D21 (slim in place):** `store` / `search` / `sources` / `capture` / `privacy` / `hud` stay where they are. About 10 coupling edges to pipeline / obsidian / quality / LLM are cut. There is **no new top-level "core" package**. New capabilities (MCP server, grants, memory, audit) go in new modules.
- Baseline = git tag `legacy-v0`, the frozen reference for comparison. After the 2026-10-10 history purge (git filter-repo + force-push) the tag points to **`f991a98`** (previously `66ddd2d`, "chore: freeze legacy baseline"). Pre-purge hashes are obsolete.

## Alternatives
- New repo for the refactor: rejected, it splits history and doubles maintenance.
- New top-level core package: rejected by D21. It would duplicate modules and make the cut harder to verify.

## Rationale
- Avoid two repos and a split history.

## Consequence
- Isolation between the new core and legacy code can no longer come from repo separation. It is enforced by an **import-linter contract** in CI:
  - core modules must not import pipeline / obsidian / quality / LLM gateway;
  - macOS-only libs (AppKit / Quartz / ApplicationServices / objc / IOKit / Vision, etc.) are allowed only in capture / hud / startup (D18).
- Cut list and blockers: [[legacy-extraction-blockers]]. Overall route: [[v1-build-by-legacy-extraction]].
- Product direction: [[keypulse-v1-local-memory-infra]].


## Timeline

- time: 2026-10-10T10:51:08
  kind: decision
  summary: "Created this page: Refactor in place in the original repo; legacy-v0 tag is the frozen baseline"
  source: Owner decision 2026-10-10
  affects: [refactor-in-place-with-legacy-tag]

- time: 2026-10-10T10:51:08
  kind: decision
  summary: Initial decision recorded
  source: Owner decision 2026-10-10
  affects: [refactor-in-place-with-legacy-tag]

- time: 2026-10-10T13:53:19
  kind: decision
  summary: "D21: slim in place, no new top-level core package, import-linter contract; legacy-v0 tag now f991a98"
  source: "Owner decision 2026-10-10 (D21)"
  affects: [refactor-in-place-with-legacy-tag]

- time: 2026-10-10T13:53:19
  kind: decision
  summary: "Legacy-v0 tag moved to f991a98 after the 2026-10-10 history purge (was 66ddd2d); D21 slim-in-place rule added"
  source: "Session 2026-10-10 (post-purge) and owner decision D21"
  affects: [refactor-in-place-with-legacy-tag, v1-build-by-legacy-extraction]
