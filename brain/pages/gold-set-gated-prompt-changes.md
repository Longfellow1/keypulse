---
id: gold-set-gated-prompt-changes
title: Prompt changes must pass the Gold Set validators before merge
category: decision
status: archived
created: "2026-10-10T10:26:12"
updated: "2026-10-10T14:52:53"
---

<!-- compiled_truth -->
## Decision
Any user-visible LLM output (daily, weekly, skill propose, summaries) follows the Gold Set flow: 2-3 candidate versions, manual comparison, a fixed baseline, and validator checks. No single-version ship, no prompt change without a validator run against baseline.

## Current baselines
- daily: v4 (`docs/golden-daily/评测-2026-05-06-v4.md`, file renamed, uncommitted). Validator score >= 97 must not regress. Decided 2026-05-25 (bf5b71f).
- weekly: W19 exec/plain (`docs/golden-weekly/评测-2026-W19-*`). Being refreshed after content fixes.
- skill propose: **no baseline yet**; blocked on skill v0.

## Tools
`keypulse/pipeline/daily_validator.py`, `weekly_validator.py`, `quality_score.py`; CLI `keypulse eval daily|weekly` (hidden).

## Why
Prompt bloat and silent regressions kept recurring (see the commit log: many "deep-fix" commits for 完成XX hallucination, placeholder overwrites, timestamp noise).


## Timeline

- time: 2026-10-10T10:26:12
  kind: decision
  summary: "Created this page: Prompt changes must pass the Gold Set validators before merge"
  source: "CLAUDE.md (gold set section); commit bf5b71f (2026-05-25); docs/golden-daily/, docs/golden-weekly/"
  affects: [gold-set-gated-prompt-changes]

- time: 2026-10-10T10:26:12
  kind: decision
  summary: captured from project history
  source: "CLAUDE.md (gold set section); commit bf5b71f (2026-05-25); docs/golden-daily/, docs/golden-weekly/"
  affects: [gold-set-gated-prompt-changes]

- time: 2026-10-10T14:52:53
  kind: reversal
  summary: "Obsolete: daily/weekly reports removed from V1 (D24). Output quality now governed by docs/KeyPulse_Eval_PRD.md."
  source: brain archive-page
  affects: [gold-set-gated-prompt-changes]
