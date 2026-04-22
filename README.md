# KeyPulse

> **A companion that helps you upgrade how you think — not a dashboard that tells you how productive you were.**

KeyPulse is a local-first activity recorder for macOS that watches what you do, then writes back to you about it. Not as a productivity coach, not as an AI assistant doing work for you — as a **pen-pal** that slowly learns your rhythm and helps you see yourself more clearly.

*[中文版 → README.zh.md](README.zh.md)*

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12+-lightgrey.svg)](https://www.apple.com/macos/)
[![Tests](https://img.shields.io/badge/tests-333%20passing-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Why this exists

Most activity trackers fall into one of three traps:

- **Dashboards** — beautiful graphs that nobody looks at twice
- **Quantified self** — "this week: 87/100" (what do you *do* with that?)
- **AI assistants** — they do your work, so you stop thinking

KeyPulse is none of these. It records the small stuff — app switches, clipboard, window titles — not to score you, but to give a patient observer enough material to eventually write back:

> *"These past three days you kept returning to this terminal — are you stuck on something, or is this the thing you actually want to build?"*

That line doesn't come on Day 1. It comes on **Day 5**, after the system has watched long enough to earn the right to ask.

---

## What it is, and what it refuses to be

| It **is** | It **refuses to be** |
|---|---|
| A pen-pal that slowly learns your patterns | A dashboard optimizing for screen-time |
| A mirror for your thinking | A productivity scoreboard |
| Your external memory — so you can revisit past selves | An AI that does work *for* you |
| Local, auditable, on your machine | A SaaS harvesting your focus data |

---

## Relationship, not features — three stages

The product unlocks by **calendar days**, not "productive days." Companionship needs real time.

| Stage | Days | Tone | Example line |
|---|---|---|---|
| **Acquaintance** | 0–3 | "I'm still learning you — but I'm paying attention." | *"Today you spent time on these things."* |
| **Familiar** | 3–7 | "I noticed something — can I check with you?" | *"You keep coming back here lately. Is that on purpose?"* |
| **Exploring** | 7+ | "I understand what you're doing, and beginning to see why." | *"Based on the past two weeks, X is getting heavier for you. Where do you want to take it next week?"* |

The real signal that the relationship has deepened isn't a feature unlock — it's when **your language shifts from "it" to "we."**

---

## The voice it speaks in

| ✗ Machine report | ✓ Pen-pal voice |
|---|---|
| `2026-04-21 · 10 events · top theme: terminal` | "You stared at the terminal 11 hours today. A little worried about your back." |
| `Productivity: 87/100` | "You seemed less cheerful this week than last — want to try a different rhythm tomorrow?" |
| `New theme detected` | "Are you working on something new? I can't quite see it yet, but I sense it brewing." |

Every user-facing string is tested against one question: *would a pen-pal write this?* If not, we rewrite it.

---

## What's inside (the engineering)

KeyPulse ships a small stack of things that are harder than they look:

### 🧠 Human/machine **speaker model**
Every event is tagged `speaker: user` (keyboard, clipboard, manual saves) or `speaker: system` (window titles, AX tree, OCR). Daily reports are split into two columns — **what you did** vs. **what the system showed** — so your voice stays the main line, and the machinery never drowns it out.

### 🔒 Privacy, by architecture (not policy text)
- **No keystroke logging** — keyboard watcher records *chunks and boundaries*, never raw keys.
- **35-app default blacklist** — password managers, messengers, banking apps. Entire events dropped before reaching disk.
- **Field-level desensitization** — emails, tokens, API keys, cards masked at normalize time.
- **Camera-aware pause** — when macOS CMIO activates (Zoom, FaceTime, Screen recording), capture pauses automatically.
- **Private-browsing detection** (Safari/Chrome/Firefox incognito windows excluded via AX title inspection).

### ♻️ Self-healing service trio
Background tools die. KeyPulse assumes this and bakes recovery into the design:

- `keypulse healthcheck` — atomic `health.json` with 6 alert codes (`DAEMON_DEAD`, `STREAM_STALE`, `SPEAKER_MISLABEL_SPIKE`, …). Runs every 10 min via `launchd`.
- **Hourly incremental Obsidian sync** — cursor-based, dedupe by event identity, **append-safe** (your `## 今日主线` narrative and `## 明天的锚点` tomorrow-plan are never overwritten once written).
- **Menu bar HUD** — single-glance status emoji prefix (✅ / ⚠️) reading directly from `health.json`.

### 📝 Obsidian as the reading surface
Daily notes, event cards, topic cards. Checkbox-based feedback (`- [ ] 确认 [ ] 否掉 [ ] 拆分`) flows back to the pipeline — zero context switch, the report *is* the feedback form.

### 🧪 Quality framework
A **Golden Set** of labeled days keeps the narrative pipeline from silently drifting as we tune thresholds. 333 tests passing, including regression coverage for the migration that once mislabeled 2,341 user events as system events.

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
│  Pipeline                                                     │
│  • Sessionizer → work-block aggregation (dual-speaker)        │
│  • Topic extraction (user events only)                        │
│  • Narrative render (LLM, with deterministic fallback)        │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Surfaces                                                     │
│  • Obsidian vault (Daily / Events / Topics / Dashboard)       │
│  • Menu-bar HUD (health + in-session prompt)                  │
│  • CLI (timeline / search / stats / export)                   │
└──────────────────────────────────────────────────────────────┘

                  launchd orchestrates four jobs:
  daemon  •  healthcheck (10m)  •  obsidian-sync-hourly  •  obsidian-sync-daily
```

---

## Quick start

```bash
# Clone and install (creates a venv at ~/.keypulse/venv, loads launchd jobs)
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse
bash install.sh

# Grant Accessibility + Screen Recording permissions when prompted, then:
keypulse start
keypulse doctor          # verify watchers, permissions, DB
keypulse hud             # menu-bar indicator (optional)

# A day later, read your report:
open ~/Go/Knowledge/Dashboard/Today.md
```

### The commands you'll actually use

| | |
|---|---|
| `keypulse timeline --today` | See today's sessions |
| `keypulse search "<query>"` | Full-text search across your own memory |
| `keypulse obsidian sync --incremental` | Append new events to today's note (runs hourly) |
| `keypulse obsidian sync --yesterday` | Generate yesterday's full narrative (runs at 09:05) |
| `keypulse healthcheck` | Atomic health report → `~/.keypulse/health.json` |
| `keypulse purge --app Slack --confirm` | Wipe everything from an app, permanently |

Full CLI reference: `keypulse --help`.

---

## Privacy summary

| What's recorded | What's never recorded |
|---|---|
| App names, window titles (outside blacklist) | Raw keystrokes |
| Clipboard text (≤ 2000 chars, deduped) | Anything from password managers / messengers / banking apps |
| Manual `keypulse save` notes | Contents during camera-active meetings |
| Session boundaries, idle periods | Private/incognito browser windows |

All data lives in `~/.keypulse/keypulse.db` on your machine. No cloud, no telemetry, no network — export is explicit.

---

## Philosophy: the three red lines

Before shipping any feature, we ask:

1. Does this **deepen the relationship** or just add a function?
2. Does this text read like a **pen-pal**, or like a **butler reporting receipts**?
3. Will the user feel *"it gets me"* or *"it's bookkeeping me"*?

Miss any one, redesign.

---

## Status

- **Platform:** macOS 12+ (Apple Silicon + Intel)
- **Tests:** 333 passing (pytest)
- **Installable:** `bash install.sh` — one-shot, idempotent, launchd-managed
- **Roadmap:** quarterly/annual memoir-style recall · cross-device merge · voice-prompted reflection

KeyPulse is early. The pen-pal can hold a conversation about your day; it can't yet hold one about your year. That's next.

---

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by [Harland](https://github.com/Longfellow1) · reach out: haolang95@gmail.com

> *The goal of KeyPulse is not to make you more efficient.*
> *It's to help you recognize who you are — and who you're becoming.*
