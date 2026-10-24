# Penpal

> **The local memory all your AIs share.** Penpal remembers what you've seen, written and asked on your Mac, and serves it over MCP to Claude Code, Codex and Cursor. Every result links back to its source, and you decide what each AI is allowed to see.

[中文](README.md) · [Quick start](#quick-start) · [What it records](#what-it-records-and-what-it-doesnt) · [Where it stands](#where-it-stands)

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12+-lightgrey.svg)](https://www.apple.com/macos/)
[![Tests](https://img.shields.io/badge/tests-1000%2B%20passing-brightgreen.svg)](tests/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## What it solves

You ask Claude Code: "Last week I read a page about MCP auth. Where was it?" It can't answer. It has never seen your browser. Even if you let it run bash, browser history expires after 90 days, and the clipboard, the windows you switched through, and what you typed into web forms were never written to disk in the first place.

With Penpal, the same question makes the AI call `search_context`. It gets back that page, which app you were in, and what you were doing before and after. Then `get_evidence` opens the original source so you can check it yourself.

Penpal is not a chat interface and has no built-in model. You keep asking questions in the AI you already use. Penpal's job is to remember, and to hand over the right thing.

## Why not just let the AI use bash

An AI can already read local files, so indexing files that already exist is not worth a separate tool. Penpal exists for three reasons only. Every feature must serve one of them and beat "AI + bash" in a side-by-side eval:

| | AI + bash | Penpal |
|---|---|---|
| **Information that never hits disk**: window switches, clipboard, text typed into web pages | Gone | Recorded at capture time |
| **Records past the source's retention**: Claude Code sessions ~30 days, Chrome history ~90 days | Gone once expired | Kept after import until you delete it |
| **Controlled access** | Giving an AI bash means opening the whole disk; Claude Desktop and sandboxed Codex have no bash at all | Per-AI grants, scoped by category, redacted before storage, revocable, every query logged |

## Two install tiers

| | CLI (required minimum) | Desktop app (optional) |
|---|---|---|
| For | Developers who live in the terminal | People who want to see capture status and manage it visually |
| Includes | Background capture + MCP server + all control commands | Menu-bar HUD: status, source toggles, AI grants, privacy and deletion |
| Install | See Quick start below | Install the CLI first, then `penpal app install` (add `--login` to start at login) |
| Core capability | Everything works in your AI once installed | Same core, different interface |

Anything the app can do, the CLI can do. The app needs no system permission of its own: it only launches `penpal hud`, and the permissions capture needs stay with the background service. A CLI-only install doesn't fail silently either: if capture stops, the disk fills up or a permission is lost, the AI receives a warning in its next query result and tells you.

## Quick start

```bash
# 1. Install the CLI, initialize the runtime, register the background service at login
curl -fsSL https://raw.githubusercontent.com/Longfellow1/penpal/main/install.sh | bash
# or manually: brew install longfellow1/penpal/penpal && penpal install init

# 2. Check system permissions: doctor lists what's missing, which program to grant, and which Settings pane
penpal doctor

# 3. Grant an AI access and add the printed config (it prints Claude Code / Codex / Cursor config)
penpal mcp grant claude --buckets Work,Personal
```

Then ask Claude Code as usual, for example "What was I researching yesterday afternoon?"

Want the menu-bar interface (optional)?

```bash
penpal app install          # builds Penpal.app in ~/Applications; add --login to start at login
penpal app uninstall        # removes the app and its login item; the daemon and your data are untouched
```

### Verified hosts

| Host | Version | Notes |
|---|---|---|
| Claude Code | 2.1.303 (`claude --version`) | Printed config checked by MCP protocol-level tests (simulated client) |
| Codex CLI | codex-cli 0.162.1 (`codex --version`) | Same as above |
| Cursor | no `cursor` command detected | Config format checked against official docs; not verified on a real machine |

## Tools the AI can use

| Tool | What it does |
|---|---|
| `search_context` | Search by keyword, time and source; reports which scopes were searched and whether each keyword matched |
| `get_timeline` | Browse what you did over a period of time |
| `get_evidence` | Open the original source of a result |
| `rate_result` | Rate a result to improve ranking |

Read-only. Whatever is returned enters that AI's conversation and may be sent to its cloud service. Grants are scoped by category (Work / Personal / Private / Financial). `penpal mcp audit` shows who queried when and how many results came back; query text is not logged.

## What it records and what it doesn't

**Recorded** (each source can be turned off individually):

- Live capture: foreground window switches, clipboard, current web page URL, keyboard text snippets
- Imports: Chrome / Safari history, Claude Code and Codex sessions, shell history, Git commits, Spotlight

**Not recorded, or excluded by default:**

- Apps on the default exclusion list, such as password managers, are not recorded. Private browsing windows record only the app and time, not content. Screen-text capture pauses while the camera is on.
- Banking and payment content goes into the Financial category automatically; an AI without a Financial grant can't see it.
- Chat apps (WeChat, Slack, Feishu, etc.) are not captured by default. Once enabled individually they go into the Private category, visible only to AIs granted Private.
- Keyboard: individual keystrokes are not stored, only filtered text snippets. On by default; like other sources they are searchable by an AI within the categories it is granted. One command turns it off (`penpal source disable keyboard_chunk`).
- The part of an email address before @ is masked (the domain is kept). Tokens, keys and card numbers are masked before storage.

Data stays on your machine in `~/.penpal/`. No cloud, no telemetry. Nothing is deleted automatically; deletion is your call, and it removes the original text, index, source and related query records together.

## Day-to-day control

| Task | Command |
|---|---|
| Status and health | `penpal status`, `penpal health`, `penpal doctor` |
| Pause, resume (pause survives restarts) | `penpal pause`, `penpal resume` |
| Toggle a source | `penpal source list`, `penpal source disable keyboard_chunk` |
| Allow a chat app | `penpal privacy chat-apps allow Slack` |
| Manage AI grants | `penpal mcp list`, `penpal mcp revoke <client>`, `penpal mcp rotate <client>` |
| Delete data (preview the count first) | `penpal purge --source chrome_history --dry-run` |

Full reference: `penpal --help`.

## Where it stands

- **Recall accuracy**: the author built a 50-question eval on real data from their own Mac; about a third of the questions give only partial clues. Answered by an independent model and graded automatically, it averaged 46.7 correct over three runs (93%), with 0 out-of-scope returns. The eval set contains personal data and is not published.
- **Works today**: capture, import, redaction, per-category grants, search, sources, deletion, pause and source toggles, failure alerts without a UI.
- **Not there yet**:
  - Keyboard snippets are reliable only for English; capturing committed text from Chinese input methods is still experimental.
  - macOS only.
  - AIs can read but cannot write back to long-term memory.
  - The desktop app is the HUD only for now; features that need a UI, such as daily and weekly reports, come later.

## License

Apache 2.0, see [LICENSE](LICENSE).

## Author

**Harland**, AI-native product manager. Email: Harland5588@outlook.com
