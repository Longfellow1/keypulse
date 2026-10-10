---
id: refactor-in-place-with-legacy-tag
title: "Refactor in place in the original repo; legacy-v0 tag is the frozen baseline"
category: decision
status: active
tags: [refactor, repo, legacy]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T10:51:08"
---

<!-- compiled_truth -->
## Decision
The KeyPulse refactor happens **in place** in the original repository (github.com/Longfellow1/keypulse). No new repository.
Baseline = git tag `legacy-v0` (commit 66ddd2d, "chore: freeze legacy baseline") and is the frozen reference for comparison and data import.

## Alternatives
- New repo for the refactor: rejected, it splits history and doubles maintenance.

## Rationale
- Avoid two repos and a split history.

## Consequence
- Isolation between the new core and legacy code can no longer come from repo separation. It must be enforced by an **import-boundary contract** (see [[legacy-extraction-blockers]]).
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
