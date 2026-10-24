---
id: runtime-contract
title: "D36: One runtime contract — daemon-written record, one evaluator, one launchd control path, per-process code identity"
category: decision
status: active
tags: [runtime, health, hud, doctor, launchd, mcp]
created: "2026-10-24T23:46:07"
updated: "2026-10-24T23:46:07"
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
