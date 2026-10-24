---
id: runtime-contract
title: "D36: One runtime contract — daemon-written record, one evaluator, one launchd control path, per-process code identity"
category: decision
status: active
tags: [runtime, health, hud, doctor, launchd, mcp]
created: "2026-10-24T23:46:07"
updated: "2026-10-25T03:49:13"
---

<!-- compiled_truth -->
Decision (D36, 2026-10-24, owner accepted all recommendations after a deep-fix audit of 80+ liveness/HUD/doctor commits since 2026-04):

- **One record**: only the daemon writes runtime state — an atomic file (not the DB, so storage faults stay visible) every ~10 s with version, code fingerprint, pid + process start time (PID-reuse safe), heartbeat, per-component state. HUD writes its own record (incl. whether the status item is actually visible).
- **One evaluator**: a single function maps records + launchd state to a closed set (not_installed / not_running / starting / running / hung / outdated / paused) plus component issues. doctor, status, HUD, MCP capture_health and the healthcheck job all call it; thresholds live only there; an import-linter contract forbids reading pid files, launchctl or runtime keys elsewhere.
- **One control path**: start/stop/restart only via launchd (kickstart -k / bootout / bootstrap); `penpal restart`; installer, upgrade, HUD restart, doctor fix and the watchdog call it. Watchdog restarts hung/outdated with backoff; success = fresh heartbeat, never 'new events appeared'.
- **Code identity**: every long-lived process (daemon, HUD, mcp serve) compares its fingerprint with the installed code each minute and reloads itself — daemon exits for launchd to relaunch, HUD re-execs, MCP re-execs with an initialize-state handoff so hosts don't notice.
- **HUD**: snapshot collection off the main thread, shows data age; Penpal.app runs a native stub that embeds Python so macOS attributes the menu bar item / permissions to Penpal, not Python.
- **Deleted, not wrapped**: pid files as liveness (flock for single instance), self-heal pid-scan-kill and event-based success, unread alerts.json / capability_status.json, dead install.sh reload, the dev installer's own plist writer, the manual-fork daemon mode, keypulse.log naming (rename + rotation).
- **Eval**: the state matrix (every record/launchd combination → one expected status, identical across all consumers) and real-process scenarios (hung via SIGSTOP, upgrade via fingerprint change, crash via kill) gate CI; HUD main thread must stay responsive with a slow reader.
- **Release**: v0.3.0-preview.1 waits for this.


## Timeline

- time: 2026-10-24T23:46:07
  kind: decision
  summary: "Created this page: D36: One runtime contract — daemon-written record, one evaluator, one launchd control path, per-process code identity"
  source: owner decision 2026-10-24 after deep-fix audit
  affects: [runtime-contract]

- time: 2026-10-24T23:46:07
  kind: decision
  summary: Rewrote compiled_truth to the new best understanding
  source: brain update-truth
  affects: [runtime-contract]

- time: 2026-10-25T01:17:24
  kind: decision
  summary: "PR1 evaluator rules settled: core-loop staleness is measured from max(last progress, last system wake via kern.waketime) so sleep/wake never reads as hung; a live launchd pid without a matching record is starting within the grace window, then hung (alive but not progressing) — not_running only when no live daemon process exists; PENPAL_ISOLATED=1 makes utils.paths refuse real-home Penpal paths and conftest sets it session-wide."
  source: D36 PR1 review 2026-10-25
  affects: [runtime-contract]

- time: 2026-10-25T03:10:23
  kind: decision
  summary: "PR2 control rules settled: penpal.runtime.control is the only launchd mutator (kickstart -k when plist unchanged, else bootout → bounded wait → bootstrap ×3); restart succeeds only when a new generation's core loop progresses. Watchdog acts on hung / outdated / not_running ONLY while launchd still manages the job (penpal stop boots it out, so a user stop is never undone); 2 consecutive failures, 15-min cooldown, 3 restarts/hour, then report-only. Single instance via flock on run/daemon.lock (second serve exits 73); PID files are diagnostics only."
  source: D36 PR2 review 2026-10-25
  affects: [runtime-contract]

- time: 2026-10-25T03:49:13
  kind: decision
  summary: "PR3 self-reload settled: identity = code fingerprint + package version + config.toml hash + DB schema version; schema newer than code → OUTDATED and the store refuses writes. Daemon exits 75 on identity change and launchd relaunches it (KeepAlive SuccessfulExit=false); HUD execv from its timer; MCP serve execs only between requests, replaying the saved initialize params into the new process and filtering the synthetic response, sends tools/list_changed when the tool list differs, and exits on stdin EOF or parent death. Depends on MCP SDK low-level stdio API → pinned mcp>=1.30,<1.31; bumping the SDK requires re-running the handoff tests and the 3-host manual check."
  source: D36 PR3 review 2026-10-25
  affects: [runtime-contract]
