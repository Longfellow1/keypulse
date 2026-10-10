---
id: session-handoff-2026-10-10
title: "Session handoff 2026-10-10: current state for a fresh session"
category: project
status: active
tags: [handoff, state, refactor]
created: "2026-10-10T11:19:23"
updated: "2026-10-10T11:56:40"
---

<!-- compiled_truth -->
## Repo state
- **HISTORY WAS REWRITTEN on 2026-10-10** (git filter-repo + force-push; all branches and tags rewritten). All commit hashes from earlier in this session are obsolete.
- Branch `main` == `origin/main` at `7ddec56` (privacy commit: real-data eval artifacts kept out of the repo).
- Tag `legacy-v0` = `f991a98` (frozen baseline; was `66ddd2d`). Pushed.
- Tag `backup/install-homebrew-clean` = `dbbc6dc` (was the pre-rewrite tag target).
- Rename-fix commit is `d0cba9f` (was `7ab8641`).
- Docs committed: PRD v1.2 with D18 / D19 and Eval PRD v0.1 (`8a955c0`). The Eval PRD is no longer untracked, but still awaits owner decisions Q1-Q3.
- Refactor is in place in github.com/Longfellow1/keypulse. Planned branch `refactor/core-v1`.
- Planned contract: import-linter. The core package must not import pipeline / obsidian / hud / quality. Enforced in CI.
- Pre-purge backup bundle (contains the leaked secrets) is at the session scratchpad. Delete it after the owner confirms.

## Test baseline (HOME redirected to temp dir)
- Originally at legacy-v0: 1243 tests, 10 failed, 1215 passed, 19 skipped.
- The 8 rename-broken tests are FIXED in `d0cba9f` (tests, docstrings, a CLI hint, `scripts/eval_entity_extractor.py`). They failed because tests and code hard-coded old paths (`docs/golden-*`, `keypulse/prompts/daily_flagship.v2.md`).
- Remaining known failures:
  - real-data test `test_daily_flagship_context_helpers_read_real_may_9_history` (depends on real `~/.keypulse` data);
  - env test `test_browser_url_watcher_disables_unsupported_dialect_without_marking_denied` (suspected macOS / env);
  - `test_path_filter_excludes_backup_and_cache` fails only when HOME is redirected to mktemp (`/private/var` resolve mismatch).
- Pointers: `docs/extraction/pytest-baseline.txt`.

## Branch state (after rewrite)
- Done:
  - `install-homebrew` and `refactor/cleaning-unify`: pushed and kept (not on main).
  - `wip-before-reset-2026-05-19`: pushed, then deleted locally.
  - `worktree-principle-vertical-v1`: pushed; worktree untouched.
  - `install-homebrew-clean`: deleted; preserved as tag `backup/install-homebrew-clean`.
  - `refactor/daily-renderer-unification`, `feat/daily-v3-m5`: deleted.
  - Stale worktrees pruned.
- To review:
  - Local branches `feat/plan-a-finalize`, `feat/weekly-v3`, `fix/py2app-and-flagship-5-7` exist locally but not on origin. `feat/weekly-v3` was recorded as deleted earlier, so it either reappeared or was never deleted. Review before deleting anything.
  - `fix/drop-keyboard-chunk` and `fix/p0-stability`: merged, but checked out in `.claude/worktrees` with uncommitted changes (24 / 22). Review before deleting.
  - `principle-vertical-v1` worktree: 118 uncommitted changes. Review first.
  - Low priority: `scripts/eval_daily_flagship_v4_candidates.py` output naming vs renamed candidate docs is unresolved.

## Housekeeping
- Parent `/Users/Harland/CLAUDE.md` describes another project (qi-agent) and loads here. Consider isolating.
- Stray scratch file `/Users/Harland/.keypulse_scratch_none` can be deleted (ask first).
- GitNexus MCP tools were not available this session, so `detect_changes` was not run for the freeze commit (rename and docs only).

## PRD v1.2 status
- B1-B8 and conflicts 1-5 are APPROVED. Details in [[v1-development-baseline-prd]]. No owner veto is pending.
- Windows (D18) and personal-chat capture (D19) are resolved; see [[v1-deferred-undecided-items]].
- Still open: camera capture, Cursor data-source format (unspecified in PRD, to confirm).

## Next steps (in order)
1. Owner security actions, from [[no-real-data-in-repo]]:
   - revoke the leaked third-party API key (anyint.ai) and check the `sk-sp-` key;
   - decide on a GitHub Support purge request for the 7 closed PR refs;
   - decide on the exposure of the public golden daily / weekly baselines.
2. Owner decides Eval PRD Q1-Q3 ([[eval-prd-separate]]): Q1 real-data labels local-only; Q2 initial thresholds, ratchet-up only; Q3 E2E recall >=70% as V1.0 gate. Then promote `docs/KeyPulse_Eval_PRD.md` to v1.0, commit, and repoint baseline PRD §5.3/§6.2/§6.4 references. The "unauthorised-access leakage = 0" HARD release gate must stay.
3. P0 breakdown:
   - core package skeleton + import-linter rules;
   - data model per PRD §3.5;
   - MCP tools `search_context` / `get_timeline` / `get_evidence` + `commit_memory`, with token + allowlist auth ([[mcp-caller-auth-p0]], [[memory-distillation-by-agent]]);
   - eval V0 infra, in parallel.
4. Extraction order unchanged, after unblocking:
   - store, then search, then privacy / permissions (these need extension per PRD, not as-is), then capture / sources.
   - Blockers: `pipeline/__init__.py` eager imports; `sources/sink.py:11` imports `quality.entity_extractor`; quality cluster strategies import `obsidian.layout`; capture <-> capabilities cycle; `app.py` / `cli.py` hard-wired to pipeline + obsidian; DB schema ownership split (`store/migrations.py` v19 vs `pipeline/triggers.py` llm_trigger_log, `pipeline/run_record.py` run_records).
   - `hud` is NOT stay-legacy: PRD §3.9 makes it a P0 trust surface.
- Full blocker list: [[legacy-extraction-blockers]]. Refactor policy: [[refactor-in-place-with-legacy-tag]]. Decisions: [[keypulse-v1-local-memory-infra]], [[v1-development-baseline-prd]], [[multi-host-mcp-integration]], [[keyboard-capture-p0-experimental]].
- Structure and dependency analysis: `docs/extraction/structure-tree.md`, `docs/extraction/dependency-map.md`, scripts in `docs/extraction/scripts/`.


## Timeline

- time: 2026-10-10T11:19:23
  kind: decision
  summary: "Created this page: Session handoff 2026-10-10: current state for a fresh session"
  source: Session 2026-10-10 handoff
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:19:23
  kind: decision
  summary: "Capture repo, test, branch and pending-op state at handoff"
  source: Session 2026-10-10
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:30:23
  kind: decision
  summary: "Next steps rewritten: PRD v1.2 B1-B8 approved, no owner veto needed; order updated; open items added"
  source: "Owner delegated to assistant recommendation, 2026-10-10"
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:43:19
  kind: decision
  summary: "Afternoon update: main pushed at 7ab8641, rename-broken tests fixed, branch cleanup done"
  source: "Session 2026-10-10 (afternoon)"
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:43:20
  kind: decision
  summary: "Eval PRD v0.1 drafted (three-layer release gates; leakage=0 via canary + differential test); rename tests fixed; main+legacy-v0 pushed; branch cleanup done"
  source: "Session 2026-10-10 (afternoon)"
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:56:40
  kind: decision
  summary: "History rewritten: all hashes changed (main=7ddec56, legacy-v0=f991a98, rename-fix=d0cba9f, backup tag=dbbc6dc); docs committed; next steps reordered after leak purge"
  source: "Session 2026-10-10 (post-purge)"
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:56:40
  kind: decision
  summary: "Leak purge: audit found a real third-party API key, WeChat excerpts and job-search traces in docs/entity-extractor-eval-2026-05.json (public since 2026-05-23) via capture -> eval -> git path. History rewritten with git filter-repo and force-pushed (all branches/tags). Residual: 7 closed PR refs (#3-#8, #15) still hold an sk-sp- key-like string; only GitHub Support can purge. Owner actions pending: key revocation, support purge, golden baseline review."
  source: "Session 2026-10-10 (post-purge)"
  affects: [session-handoff-2026-10-10, no-real-data-in-repo]
