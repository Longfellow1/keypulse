---
id: no-real-data-in-repo
title: Real captured data never enters the git repo
category: decision
status: active
tags: [privacy, eval, git, secrets]
created: "2026-10-10T11:56:26"
updated: "2026-10-10T11:56:44"
---

<!-- compiled_truth -->
## Rule
- Any artifact derived from real captured data (eval sets, labels, golden outputs built from `~/.keypulse`) stays under `~/.keypulse/eval/` locally.
- Only aggregate numbers (scores, counts, rates) may be committed to the repo.
- `.gitignore` covers `docs/*-eval-*.json`.

## Why
- On 2026-10-10 an audit found `docs/entity-extractor-eval-2026-05.json` (public since 2026-05-23) contained:
  - a real third-party API key that had been pasted into a Codex chat;
  - WeChat group chat excerpts;
  - job-search traces.
- The data leaked through KeyPulse's own capture -> eval -> git path.
- Response: history was purged with `git filter-repo` and force-pushed; all branches and tags were rewritten.
- Proof point: sensitive-data filtering is best-effort only. It cannot be the only control, so the control is to keep real-data artifacts out of git entirely.

## Residual exposure (not yet closed)
- 7 closed PR refs on GitHub (#3-#8, #15) still contain an `sk-sp-` key-like string in the history of `tests/golden/cluster_topics.jsonl`. These refs are read-only; only GitHub Support can purge them.
- Old commit SHAs may stay viewable on GitHub until GitHub GC or a support purge.
- Golden daily and weekly baselines (`docs/golden-daily`, `docs/golden-weekly`) are real-data-derived owner activity summaries and are still public. Pending owner review.
- The leaked third-party key must be revoked by the owner. Still pending (owner action; see [[session-handoff-2026-10-10]]).

## Links
- [[eval-prd-separate]] — eval data handling (Q1: real-data labels local-only).
- [[keyboard-capture-p0-experimental]] — the capture path that produced the leaked material.
- [[session-handoff-2026-10-10]] — current state and next steps.


## Timeline

- time: 2026-10-10T11:56:26
  kind: decision
  summary: "Created this page: Real captured data never enters the git repo"
  source: Owner decision 2026-10-10
  affects: [no-real-data-in-repo]

- time: 2026-10-10T11:56:26
  kind: decision
  summary: "Rule established: real-data-derived artifacts stay local; history purged after leak"
  source: "Owner decision 2026-10-10; audit 2026-10-10"
  affects: [no-real-data-in-repo]

- time: 2026-10-10T11:56:44
  kind: decision
  summary: Clarify that owner revocation of the leaked key is still pending
  source: "Owner decision 2026-10-10; audit 2026-10-10"
  affects: [no-real-data-in-repo]
