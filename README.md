# KeyPulse

**Local-first personal activity memory for macOS**

KeyPulse is a lightweight, privacy-first daemon that records what you're doing—applications, windows, clipboard contents, manual notes—into a local SQLite database. It provides powerful CLI tools to search, recall, and analyze your work history with intelligent privacy protection.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12.0+-lightgrey.svg)](https://www.apple.com/macos/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## ✨ Why KeyPulse?

- **Never lose context** — Automatically record what you're working on; search and recall your activity history anytime
- **Privacy by default** — All data stays on your machine. Sensitive apps are never monitored. Passwords and tokens are automatically masked
- **Lightweight daemon** — Uses < 0.5% CPU and 30 MB memory; negligible battery impact (~1%/hour)
- **Powerful search** — Full-text search over clipboard history, manual notes, and session summaries with natural language queries

## 🚀 Quick Start

### Requirements

- macOS 12.0 or later
- Python 3.11 or later
- Accessibility permissions (granted on first run)

### Installation

```bash
bash install.sh --no-launchd
```

The installer now auto-detects the best local knowledge sink, binds to an open Obsidian vault when one is present, and falls back to standalone markdown output when it is not.

### Start the daemon

```bash
keypulse start
```

The daemon runs in the background and auto-starts if your system reboots (requires login item setup).

### Query your activity

```bash
# View today's timeline
keypulse timeline --today

# Search for something you worked on
keypulse search "activitywatch" --since 7d

# See recent clipboard contents
keypulse recent --type clipboard

# View work sessions
keypulse session list --today
```

### View statistics

```bash
# Weekly activity breakdown
keypulse stats --days 7

# Export as JSON for analysis
keypulse export --format json --output report.json

# Export into an Obsidian vault
keypulse export --format obsidian --output ~/Go/Knowledge

# Run the daily Obsidian sync manually
keypulse obsidian sync --yesterday

# Inspect the active sink binding
keypulse sinks status

# Build a daily draft from the raw event stream
keypulse pipeline draft --date 2026-04-18

# Record feedback on a topic or draft
keypulse pipeline feedback add --kind promote --target decision-making --note "repeat topic"
```

## 中文命令速查

日常最常用的一组命令如下：

| 场景 | 命令 | 说明 |
|------|------|------|
| 启动采集 | `keypulse start` | 在后台启动主守护进程 |
| 停止采集 | `keypulse stop` | 停止主守护进程；若是 `launchd` 托管，则一并卸载托管 |
| 查看状态 | `keypulse status` | 查看运行状态、PID、数据库信息，以及是否由 `launchd` 托管 |
| 系统检查 | `keypulse doctor` | 检查依赖、权限和配置 |
| 启动 HUD | `keypulse hud` 或 `keypulse hud start` | 启动菜单栏 HUD |
| 关闭 HUD | `keypulse hud stop` | 停止正在运行的 HUD |
| HUD 状态 | `keypulse hud status` | 查看 HUD 是否在运行 |
| 生成 pipeline 草稿 | `keypulse pipeline draft --date 2026-04-18` | 生成某一天的确定性草稿 |
| 运行 pipeline 同步 | `keypulse pipeline sync --yesterday` | 执行统一同步流程 |
| Obsidian 同步 | `keypulse obsidian sync --yesterday` | 导出每日 Obsidian bundle |

说明：

- 如果你是通过安装脚本启用了 `launchd`，那么 `keypulse start` / `keypulse stop` 会优先控制 `launchd` 托管，而不是只杀单个进程。
- `keypulse hud stop` 现在会兼容旧版未写入 PID 文件的 HUD 进程。

## 📖 Full Command Reference

Core commands, organized by function:

### Daemon Control

| Command | Function |
|---------|----------|
| `keypulse start` | Start the background daemon |
| `keypulse serve` | Run in the foreground under launchd or another supervisor |
| `keypulse stop` | Stop the daemon; unload launchd supervision when applicable |
| `keypulse pause` | Pause recording (keep daemon running) |
| `keypulse resume` | Resume recording |
| `keypulse status` | Show daemon status, uptime, and whether launchd supervision is active |
| `keypulse doctor` | Check system dependencies and config |

### HUD Control

| Command | Function |
|---------|----------|
| `keypulse hud` | Launch the macOS status bar HUD |
| `keypulse hud start` | Launch the HUD explicitly |
| `keypulse hud stop` | Stop the running HUD instance |
| `keypulse hud close` | Alias for `keypulse hud stop` |
| `keypulse hud status` | Show HUD process status |

### Recording

| Command | Function |
|---------|----------|
| `keypulse save <text>` | Manually save a note (e.g., `keypulse save "Meeting notes: discussed Q2 roadmap"`) |

### Querying Activity

| Command | Function |
|---------|----------|
| `keypulse timeline` | Show activity timeline by session (today by default) |
| `keypulse timeline --date 2026-04-10` | Show activity for a specific date |
| `keypulse recent` | Show 10 most recent clipboard copies, manual notes, and sessions |
| `keypulse recent --type clipboard` | Show only recent clipboard entries |
| `keypulse recent --type manual` | Show only manual saves |
| `keypulse recent --limit 20` | Show 20 items instead of 10 |
| `keypulse search <query>` | Full-text search across clipboard, notes, and sessions |
| `keypulse search "ActivityWatch" --since 7d` | Search the last 7 days |
| `keypulse search "python" --app VSCode` | Limit search to a specific app |
| `keypulse search "todo" --source clipboard` | Search only clipboard history |

### Sessions & Stats

| Command | Function |
|---------|----------|
| `keypulse session list` | Show all sessions (today by default) |
| `keypulse session list --date 2026-04-10` | Sessions for a specific date |
| `keypulse session <id>` | Show details of a specific session (window titles, app time, etc.) |
| `keypulse stats` | Show activity summary (today by default) |
| `keypulse stats --days 7` | Weekly statistics (time per app, idle percentage, etc.) |

### Data Management

| Command | Function |
|---------|----------|
| `keypulse export` | Export today's data as JSON |
| `keypulse export --format csv --days 7 --output report.csv` | Export 7 days as CSV |
| `keypulse export --format markdown --output report.md` | Export as Markdown |
| `keypulse purge --today` | Delete today's data |
| `keypulse purge --last-hours 12 --app "1Password"` | Delete recent 1Password data |
| `keypulse purge --app Slack --confirm` | Permanently delete all Slack recordings |

### Configuration & Rules

| Command | Function |
|---------|----------|
| `keypulse config show` | Display current configuration |
| `keypulse config path` | Show config file location (~/.keypulse/config.toml) |
| `keypulse obsidian sync` | Export a daily Obsidian bundle |
| `keypulse sinks detect` | Auto-detect and optionally persist the active sink binding |
| `keypulse sinks status` | Show the current sink binding |
| `keypulse rules list` | Show all privacy policies |
| `keypulse rules add --app 1Password --mode deny` | Add a rule: never record this app |
| `keypulse rules add --app Slack --mode metadata-only` | Record only app name, not window title |
| `keypulse rules disable <rule-id>` | Temporarily disable a rule |

### Information Pipeline

| Command | Function |
|---------|----------|
| `keypulse pipeline draft` | Build a deterministic daily draft from raw events |
| `keypulse pipeline feedback add` | Append feedback for a topic, event, or draft |
| `keypulse pipeline feedback list` | List recorded feedback events |

## 🔒 Privacy & Security

### What's recorded

- **Application names** — e.g., "VSCode", "Safari"
- **Window titles** — e.g., "VSCode — keypulse/cli.py", "Safari — GitHub | KeyPulse"
- **Clipboard contents** — Text you copy (up to 2000 characters per copy event by default)
- **Manual notes** — Text you explicitly save with `keypulse save`
- **Session metadata** — Duration, idle/active periods, keystroke density

### What's NOT recorded

- **Keyboard input** — Raw keystrokes are never captured
- **Sensitive apps** — 1Password, Keychain Access, LastPass, Bitwarden, KeePassXC, and many others are blacklisted by default
- **Passwords & tokens** — Automatically detected by pattern matching (email addresses, API keys, credit card numbers) and masked
- **Chat app contents** — Slack, Teams, Discord, iMessage — app names are recorded but not content
- **Browser content** — While Safari window titles are recorded, web page contents are not

### Privacy controls

**Default blacklist** includes:
- 1Password, Keychain, LastPass, Bitwarden, KeePassXC
- Slack, Teams, Discord, iMessage, WeChat, Signal
- Mail, Outlook, Gmail (web)
- Most password managers and security tools

**Configurable policies** let you choose per-app behavior:
- `deny` — Never record anything from this app
- `metadata-only` — Record app name and timestamps, but not window titles
- `redact` — Record everything but mask sensitive patterns (emails, tokens)
- `allow` — Record everything (default for allowed apps)

**Intelligent content masking** detects and masks:
- Email addresses: `john@example.com` → `[EMAIL]`
- Tokens/API keys: `sk-abc123...` → `[TOKEN]`
- Credit card numbers: `4111-1111-1111-1111` → `[CARD]`
- Phone numbers: `+1-555-0123` → `[PHONE]`
- Custom regex patterns via config

**Local-first architecture** ensures:
- All data stays on your machine; zero cloud uploads
- Zero tracking of your activity by KeyPulse or any third party
- No network communication except during optional export

**Manual purge** commands let you delete data anytime:
```bash
# Delete all data from today
keypulse purge --today --confirm

# Delete last 12 hours
keypulse purge --last-hours 12 --confirm

# Delete all data from a specific app
keypulse purge --app Slack --confirm
```

## 🏗️ Architecture

KeyPulse consists of modular components working together:

```
┌──────────────────────────────────────────────────────────────┐
│ Background Daemon (run by: keypulse start)                   │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─ Watchers ──────────────────────────────────────────┐    │
│  │ • Window watcher (NSWorkspace + Accessibility API) │    │
│  │ • Idle detector (CGEventSource)                     │    │
│  │ • Clipboard monitor (NSPasteboard)                  │    │
│  │ • Manual input (CLI commands)                       │    │
│  └──────────────────────────────────────────────────────┘    │
│                            ↓                                  │
│  ┌─ Capture Manager ────────────────────────────────────┐    │
│  │ Batches events, normalizes, applies policies        │    │
│  └──────────────────────────────────────────────────────┘    │
│                            ↓                                  │
│  ┌─ Privacy Layer ──────────────────────────────────────┐    │
│  │ • Pattern detection (emails, tokens, etc.)          │    │
│  │ • Intelligent desensitization                       │    │
│  │ • App blacklist enforcement                         │    │
│  └──────────────────────────────────────────────────────┘    │
│                            ↓                                  │
│  ┌─ Storage (SQLite) ────────────────────────────────────┐   │
│  │ ~/.keypulse/keypulse.db                              │   │
│  │ • raw_events — Captured clipboard, app switches     │   │
│  │ • search_docs — FTS5 indexed content               │   │
│  │ • sessions — Aggregated activity periods           │   │
│  │ • state — Daemon config and runtime state          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
└──────────────────────────────────────────────────────────────┘

                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Query Layer (CLI commands)                                   │
├──────────────────────────────────────────────────────────────┤
│  • Timeline builder — Sessions grouped by app/window        │
│  • Search engine — FTS5 full-text queries                  │
│  • Stats aggregator — Time per app, idle percentage        │
│  • Export formatter — JSON, CSV, Markdown                   │
└──────────────────────────────────────────────────────────────┘
```

### Core Components

**Daemon** (`app.py`)
- Lifecycle management (start, stop, pause, resume)
- Watcher coordination
- Event batching and flushing
- Error handling and recovery

**Watchers** (`capture/`)
- `WindowWatcher` — Detects app switches via NSWorkspace notifications
- `IdleDetector` — Tracks idle time using CGEventSource
- `ClipboardWatcher` — Monitors clipboard changes via NSPasteboard
- `ManualCapture` — CLI-driven manual saves
- `CaptureManager` — Coordinates all watchers, applies policies

**Privacy Layer** (`privacy/`)
- `Desensitizer` — Pattern detection and redaction
- Pattern matching for emails, tokens, URLs, etc.
- Configurable regex-based masking
- App blacklist enforcement

**Storage** (`store/`)
- SQLite database with FTS5 full-text search
- `raw_events` table for clipboard and app events
- `search_docs` table for indexed content
- `sessions` table for aggregated activity
- Automatic retention (default 30 days)

**Search** (`search/`)
- FTS5 query builder
- Ranking by recency and relevance
- Time-range filtering
- Per-app filtering

**Services** (`services/`)
- `timeline.py` — Formats activity as sessions with app names and window titles
- `stats.py` — Aggregates CPU/idle time per app, generates summaries
- `export.py` — Exports to JSON, CSV, Markdown
- `sessionizer.py` — Groups events into sessions (continuous app usage)

### Data Flow

1. **Capture** — Watchers detect app switches, clipboard changes, keystroke activity
2. **Normalize** — `CaptureManager` standardizes event format, applies rules
3. **Desensitize** — `PrivacyLayer` masks sensitive patterns, enforces blacklist
4. **Store** — Events written to SQLite in batches (every 5 seconds)
5. **Index** — FTS5 index updated incrementally for fast search
6. **Query** — CLI commands read from database, format results
7. **Export** — Results rendered as tables, JSON, CSV, or Markdown

## Docs

- [Open source knowledge pipeline survey](docs/research/2026-04-18-open-source-knowledge-pipeline-survey.md)
- [KeyPulse architecture decision](docs/architecture/2026-04-18-keypulse-architecture-decision.md)

## 📊 Performance

Benchmarks on a MacBook Pro (M1, 16GB RAM):

| Scenario | CPU | Memory | Disk/Week |
|----------|-----|--------|-----------|
| Idle | < 0.1% | 18 MB | ~100 KB |
| Light usage (email, browsing) | < 0.3% | 24 MB | ~600 KB |
| Normal workday (development) | < 0.5% | 35 MB | ~1.2 MB |
| Heavy usage (continuous typing) | < 1% | 50 MB | ~2 MB |

**Battery impact:** Negligible, typically < 1% per hour on laptops.

## 📝 Configuration

Configuration file location: `~/.keypulse/config.toml`

If the file doesn't exist, defaults are used. You can generate a default config:

```bash
mkdir -p ~/.keypulse
# Copy the included config.toml, or edit after first run
```

### Example config.toml

```toml
[app]
db_path = "~/.keypulse/keypulse.db"
log_path = "~/.keypulse/keypulse.log"
flush_interval_sec = 5
retention_days = 30

[watchers]
window = true
idle = true
clipboard = true
manual = true
browser = false

[idle]
threshold_sec = 180

[clipboard]
max_text_length = 2000
dedup_window_sec = 600

[privacy]
redact_emails = true
redact_phones = true
redact_tokens = true

# Explicit policies override defaults
# Each policy has: scope_type (app/domain), scope_value, mode, priority
```

### Configuration options

**[app]**
- `db_path` — Where to store the SQLite database (default: `~/.keypulse/keypulse.db`)
- `log_path` — Daemon log file (default: `~/.keypulse/keypulse.log`)
- `flush_interval_sec` — How often to write batched events (default: 5 seconds)
- `retention_days` — Auto-delete records older than this (default: 30 days)

**[watchers]**
- `window` — Monitor app switches and window titles (default: `true`)
- `idle` — Track idle time (default: `true`)
- `clipboard` — Record clipboard copies (default: `true`)
- `manual` — Allow `keypulse save` commands (default: `true`)
- `browser` — Track browser tab titles (default: `false`, not yet implemented)

**[idle]**
- `threshold_sec` — Seconds without events before marking idle (default: 180 = 3 minutes)

**[clipboard]**
- `max_text_length` — Only record clipboard entries up to this length (default: 2000)
- `dedup_window_sec` — Ignore duplicate copies within this window (default: 600 = 10 minutes)

**[privacy]**
- `redact_emails` — Mask email addresses (default: `true`)
- `redact_phones` — Mask phone numbers (default: `true`)
- `redact_tokens` — Mask API keys, tokens, credentials (default: `true`)

## 🛠️ Development

### Project structure

```
keypulse/
├── cli.py                      # 20 CLI commands
├── app.py                      # Daemon lifecycle (start, stop, daemonize)
├── config.py                   # Configuration loading and validation
│
├── capture/
│   ├── manager.py              # Coordinates all watchers
│   ├── window.py               # Window/app switch watcher
│   ├── idle.py                 # Idle time detector
│   ├── clipboard.py            # Clipboard monitor
│   ├── normalizer.py           # Event normalization
│   └── __init__.py
│
├── store/
│   ├── db.py                   # Database initialization
│   ├── models.py               # Pydantic models
│   ├── repository.py           # Database queries (CRUD)
│   └── __init__.py
│
├── privacy/
│   ├── desensitizer.py         # Pattern detection and masking
│   ├── patterns.py             # Regex patterns for sensitive data
│   ├── blacklist.py            # App blacklist
│   └── __init__.py
│
├── search/
│   ├── engine.py               # FTS5 search builder and executor
│   └── __init__.py
│
├── services/
│   ├── timeline.py             # Timeline formatting
│   ├── stats.py                # Statistics aggregation
│   ├── export.py               # JSON/CSV/Markdown export
│   ├── sessionizer.py          # Event-to-session grouping
│   └── __init__.py
│
└── utils/
    ├── logging.py              # Structured logging
    ├── paths.py                # Path helpers (~/.keypulse)
    ├── lock.py                 # Single-instance daemon lock
    └── __init__.py
```

### Running locally

```bash
# Clone and install in development mode
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse
pip install -e .

# Check that dependencies are available
keypulse doctor

# Start the daemon
keypulse start

# Query activity
keypulse timeline --today
keypulse search "something"

# View logs
tail -f ~/.keypulse/keypulse.log

# Stop the daemon
keypulse stop
```

### macOS permissions

KeyPulse requires:
- **Accessibility** permission to monitor window titles and idle time
- **Screen Recording** permission to track active windows (on macOS 13+)

When you first run `keypulse start`, the daemon requests these permissions. You can also grant them manually:

```
System Settings → Privacy & Security → Accessibility → Add Python
System Settings → Privacy & Security → Screen Recording → Add Python
```

### Testing

To verify the daemon is working:

```bash
# Check daemon status
keypulse status

# View recent clipboard
keypulse recent --type clipboard

# Search for test data
keypulse save "Test note from development"
keypulse search "development"

# Export and inspect
keypulse export --format json | jq .
```

For unit tests (if added to the project):

```bash
python -m pytest tests/ -v
```

## ⚖️ License

MIT License — See [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Areas of particular interest:

- **Linux/Windows watcher implementations** — Extend KeyPulse to other OSs
- **Additional privacy patterns** — Improve detection of sensitive data
- **Enhanced search indexing** — Better ranking, semantic search
- **Performance optimizations** — Reduce memory/CPU footprint further
- **Documentation improvements** — Help others understand the codebase

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## FAQ

### Does KeyPulse upload data to the cloud?

No. All data stays on your machine. There is no network communication except when you explicitly export data.

### Can I trust the privacy protection?

Yes. The code is open source and auditable. We default to NOT recording content and only keep what you explicitly enable. Sensitive data patterns are detected locally and masked before storage. You can review the privacy rules and customize them via `~/.keypulse/config.toml`.

### What about CPU and battery impact?

Negligible. The daemon uses event-driven architecture (not polling), batches writes, and sleeps most of the time. Typical impact is < 0.5% CPU and < 1% battery per hour.

### How far back does history go?

By default, 30 days (configurable via `retention_days` in config). Older records are automatically deleted to bound database size.

### Can I export my data?

Yes. Use `keypulse export --format json` to export as JSON, CSV, or Markdown. Data is yours to keep and analyze.

### What if I want to disable monitoring?

Use `keypulse pause` to temporarily stop recording without stopping the daemon. Use `keypulse stop` to fully shut down. Data is preserved.

### Can I run KeyPulse on multiple machines?

Currently, each machine runs its own isolated instance. You can export data from each and merge the exports manually if needed.

### Is there AI-assisted recall available?

Yes! See the [`claude/skill-api`](https://github.com/Longfellow1/keypulse/tree/claude/skill-api) branch for Claude Code and OpenClaw integration with the `work-recall` skill.

## Related

- **Skill Integration Branch** — For Claude Code / OpenClaw integration, see [`claude/skill-api`](https://github.com/Longfellow1/keypulse/tree/claude/skill-api) branch
- **GitHub Repository** — [github.com/Longfellow1/keypulse](https://github.com/Longfellow1/keypulse)
- **Security Policy** — [SECURITY.md](SECURITY.md)

---

**Last updated:** April 18, 2026
