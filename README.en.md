# Penpal

> **Penpal is a local memory layer for AI agents: it captures your activity on this Mac and serves it to Claude / Codex / Cursor over the Model Context Protocol (MCP), with evidence and access control.**

Penpal (笔友, "pen pal") quietly records your work activity — the apps you switch between, the windows you visit, clipboard boundaries, manual notes — and forgets sensitive content before it is stored.

Not a productivity dashboard.
Not a screen-time tracker.
Not a cloud surveillance assistant.

A private thinking mirror for knowledge workers.

[中文版](README.md) · [Quick start](#quick-start) · [Privacy](#privacy-summary) · [Status](#status)

---

## Why star this repo?

Star Penpal if you care about:

- local-first personal AI
- privacy-preserving activity intelligence
- AI tools that help you think instead of replacing thought

---

## Longer introduction

Penpal is a local-first tool for macOS. It captures your work activity on your own machine — the apps you switch to, the lines you paste, the windows you keep returning to — and forgets sensitive content before it reaches disk. Today you can read what it has captured through the CLI and the menu-bar HUD. Serving that memory read-only to Claude / Codex / Cursor over MCP (a standard protocol that lets AI tools read external context) is **available**, with per-AI grants.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12+-lightgrey.svg)](https://www.apple.com/macos/)
[![Tests](https://img.shields.io/badge/tests-800%2B%20passing-brightgreen.svg)](tests/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## Why this exists

Most activity trackers fall into one of three traps:

- **Dashboards** — beautiful graphs that nobody looks at twice
- **Quantified self** — "this week: 87/100" (what do you *do* with that?)
- **AI assistants** — they do your work, so you stop thinking

Penpal is none of these. It records the small stuff — app switches, clipboard, window titles — not to score you, but to give you and your AI agents a memory you can look back on.

---

## What it looks like today

<p align="center">
  <img src="docs/images/hud-v1.png" alt="Penpal HUD" width="320" />
</p>

Once installed, Penpal lives as **a small window in your menu bar.**

Open it once a day: top-left is *"the thing that matters today"* — the anchor you set yourself in the morning. Below: four numbers showing what's accumulated. Below that: three cards it picked out as worth a second look. The footer line — *"day N of recording with you"* — counts the days you've walked together.

When something breaks, it doesn't throw an error code at you. A yellow bar appears at the top of the HUD with **a button next to it**: *"Accessibility permission missing [Open Settings]"* — one click takes you straight to the right system pane. **A path to fix, not a stack trace.**

---

## What it is, and what it refuses to be

| It **is** | It **refuses to be** |
|---|---|
| A pen-pal that slowly learns your patterns | A dashboard optimizing for screen-time |
| A mirror for your thinking | A productivity scoreboard |
| Your external memory — so you can revisit past selves | An AI that does work *for* you |
| Local, auditable, on your machine | A SaaS harvesting your focus data |

---

## What's inside (the engineering)

Penpal ships a small stack of things that are harder than they look:

### 🧠 Human/machine **speaker model**
Every event carries a source tag: `speaker: user` (keyboard, clipboard, manual saves) or `speaker: system` (window titles, AX tree, OCR). Your input and system signals are distinguishable from the start, so machine signals never pass as your own input.

### 🔒 Privacy, by architecture (not policy text)
- **No keystroke logging** — keyboard watcher records *chunks and boundaries*, never raw keys.
- **35-app default blacklist** — password managers, messengers, banking apps. Entire events dropped before reaching disk.
- **Field-level desensitization** — emails, tokens, API keys, cards masked at normalize time.
- **Camera-aware pause** — when macOS CMIO activates (Zoom, FaceTime, Screen recording), capture pauses automatically.
- **Private-browsing detection** (Safari/Chrome/Firefox incognito windows excluded via AX title inspection).

### ♻️ When something breaks, you get a fix path — not an error code
Background tools die. Permissions get revoked. Penpal assumes this and bakes recovery into the design:

- **Multiple capability domains, each self-checking** — accessibility permission, capture runtime, clipboard watcher, health freshness, and more. Probed every 60s, isolated from each other. When anything fails, the HUD shows **a plain-language reason + one fix button** — never a raw error code that sends you to Google.
- **launchd manages two jobs** — daemon (auto-start on login, auto-restart on crash) and healthcheck (every 10 minutes).

### 🧪 Quality framework
800+ tests passing, including regression coverage for the migration that once mislabeled 2,341 user events as system events, plus architectural invariants for the capability framework.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Capture layer                                                │
│  • Window / AX-text / OCR   (speaker: system)                 │
│  • Keyboard-chunk / Clipboard / Manual  (speaker: user)       │
│  • Camera monitor → pauses AX+OCR when CMIO active            │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Privacy layer                                                │
│  • Blacklist (bundle IDs + glob) • Field desensitization      │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Storage — SQLite (raw_events, sessions, FTS5)                │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Surfaces                                                     │
│  • Menu-bar HUD (WKWebView · today's focus + fix-path on err) │
│  • CLI (timeline / search / stats / export)                   │
│  • MCP server (read-only) → Claude / Codex / Cursor          │
└──────────────────────────────────────────────────────────────┘

       ↑ Capability self-check (60s loop, domains independently probed)
       ↑ launchd orchestrates two jobs: daemon  •  healthcheck (10m)
```

---

## Quick start

Two steps to install — after that, it just lives there.

```bash
# 1. Install — Homebrew installs the CLI; Penpal initializes the runtime
curl -fsSL https://raw.githubusercontent.com/Longfellow1/keypulse/main/install.sh | bash

# Or manually:
brew tap Longfellow1/keypulse
brew install penpal
penpal install init

# 2. Grant Accessibility + Screen Recording in System Settings.
#    (The HUD's yellow bar will tell you exactly where to click.)
```

It now lives in your menu bar permanently. Survives reboot, restarted by `launchd` if it crashes. **Give it a while**, then run `penpal timeline --today` to see today's sessions.

### The commands you'll actually use

| | |
|---|---|
| `penpal timeline --today` | See today's sessions |
| `penpal search "<query>"` | Full-text search across your own memory |
| `penpal healthcheck` | Atomic health report (launchd runs it every 10 min) |
| `penpal purge --app Slack --confirm` | Wipe everything from an app, permanently |

Full CLI reference: `penpal --help`. Install details: [docs/说明-homebrew-install.md](docs/说明-homebrew-install.md). First-time configuration details: [docs/说明-setup-onboarding.md](docs/说明-setup-onboarding.md).

---

## Privacy summary

| What's recorded | What's never recorded |
|---|---|
| App names, window titles (outside blacklist) | Raw keystrokes |
| Clipboard text (≤ 2000 chars, deduped) | Anything from password managers / messengers / banking apps |
| Manual `penpal save` notes | Contents during camera-active meetings |
| Session boundaries, idle periods | Private/incognito browser windows |

All data lives in `~/.penpal/penpal.db` on your machine. No cloud, no telemetry, no network — export is explicit.

---

## Philosophy: the three red lines

Before shipping any feature, we ask:

1. Does this **deepen the relationship** or just add a function?
2. Does this text read like a **pen-pal**, or like a **butler reporting receipts**?
3. Will the user feel *"it gets me"* or *"it's bookkeeping me"*?

Miss any one, redesign.

---

## Connect an AI (MCP)

Penpal runs as an MCP server (`penpal mcp serve`, stdio). Each AI client is granted separately, read-only, and can be limited to buckets (Work / Personal / Private / Financial):

```bash
penpal mcp grant claude --buckets Work,Personal          # default --host claude
penpal mcp grant codex  --host codex --buckets Work
penpal mcp grant cursor --host cursor --buckets Work
```

The token is shown once (only its hash is stored locally), followed by that client's config: the `claude mcp add` command for Claude Code, plus a JSON snippet for a project `.mcp.json` (Claude Code) or `claude_desktop_config.json` (Claude Desktop); a `~/.codex/config.toml` block for Codex; `~/.cursor/mcp.json` for Cursor; `--host generic` prints plain JSON. The command in the config uses penpal's absolute path, so it starts outside your shell PATH.

Tools today: `search_context` (keyword / time / source search, with a coverage note), `get_timeline`, `get_evidence` (fetch the original source), `rate_result` (feedback on a result).

- Manage grants: `penpal mcp list`, `penpal mcp rotate <client>` (new token), `penpal mcp revoke <client>`, `penpal mcp audit` (call log; no query text or tokens)
- Note: content Penpal returns to an AI enters that AI's conversation and may be sent to its cloud model service.

---

## Status

- **Platform:** macOS 12+ (Apple Silicon + Intel)
- **Tests:** 800+ passing (pytest, including capability framework architectural invariants)
- **Installable:** Homebrew installs the CLI; `penpal install init` initializes runtime + launchd
- **First-time setup:** no model configuration needed; follow the HUD's yellow bar to grant permissions
- **MCP server:** available (read-only) — Claude / Codex / Cursor read your memory over MCP, with evidence and per-AI access control
- **Roadmap:** cross-device merge

Penpal is early: capture, search, timeline, the menu-bar HUD and the read-only MCP server work today.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Author
Built by [Harland](https://github.com/Longfellow1) · reach out: Harland5588@outlook.com

More from the author: [CorpusFlow](https://github.com/Longfellow1/CorpusFlow) (data tooling for LLM fine-tuning and eval) · agent skills [Principled-Simplicity-skill](https://github.com/Longfellow1/Principled-Simplicity-skill) and [Product-Judgment-skill](https://github.com/Longfellow1/Product-Judgment-skill)

> *The goal of Penpal is not to make you more efficient.*
> *It's to help you recognize who you are — and who you're becoming.*
