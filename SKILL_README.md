# KeyPulse + Claude Code / OpenClaw Integration

**Query your local activity history with AI assistance using Claude Code or OpenClaw**

KeyPulse now integrates with Claude Code and OpenClaw through the `work-recall` skill. Ask Claude about your recent work, and it automatically queries your local activity history to provide informed, contextual answers—all without leaving your IDE or agent.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-12.0+-lightgrey.svg)](https://www.apple.com/macos/)
[![Skill](https://img.shields.io/badge/Claude%20Code-Skill-9333ea.svg)](https://github.com/Longfellow1/keypulse/tree/claude/skill-api)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## ✨ What's the skill?

The `work-recall` skill lets Claude Code or OpenClaw query your local activity history when you ask work-related questions:

- "What was I researching last week?"
- "Show me what I copied about ActivityWatch"
- "What did I work on today?"
- "Help me pick up where I left off yesterday"
- "What apps did I use for the X project?"

Claude/OpenClaw then synthesizes your activity history into natural, helpful answers without leaving your development context.

## 🚀 How it works

```
You: "I forgot where I saw that article about ActivityWatch"
  ↓
Claude Code / OpenClaw detects this is a work-recall question
  ↓
Invokes: keypulse recall "activitywatch" --since 7d --limit 5
  ↓
Returns your recent clipboard, sessions, and searches
  ↓
Claude synthesizes into a natural answer with specific timestamps and context
```

## 🔧 Setup

### 1. Install KeyPulse CLI

```bash
git clone https://github.com/Longfellow1/keypulse.git
cd keypulse
pip install -e .
```

### 2. Start the daemon

The skill only works when the daemon is running:

```bash
keypulse start
```

You can verify the daemon is running:

```bash
keypulse status
```

### 3. For Claude Code (Built-in)

The skill file `.claude/skills/work-recall/SKILL.md` is already in the repo.

**To use it:**

1. Open this repository in Claude Code (web or CLI)
2. Type `/work-recall ` followed by your question
3. Claude will query your local activity and respond naturally

**Example:**

```
/work-recall what was I working on yesterday?
```

**Output:**

Claude synthesizes your activity history:
```
Yesterday you worked on:

1. KeyPulse CLI development (VSCode, 2h 30m)
   - Around 14:00-16:30, focused on implementing the recall command
   - Copied code snippets about FTS5 search integration
   - Made commits updating the CLI structure

2. Documentation review (Safari, 45m)
   - Around 13:15-14:00, reviewed ActivityWatch architecture
   - Copied content about event-driven watchers

You were most active in the afternoon with strong focus periods (low idle time).
```

### 4. For OpenClaw

Copy the skill file to your OpenClaw skills directory:

```bash
cp .claude/skills/work-recall/SKILL.md ~/.openclaw/skills/work-recall/SKILL.md
```

Then in OpenClaw, use `/work-recall` the same way:

```
/work-recall what did I research about machine learning?
```

## 📋 CLI Command: `keypulse recall`

You can also use the recall command directly from the CLI for testing or automation:

```bash
keypulse recall "activitywatch" --since 7d --limit 5
```

### Output format (LLM-optimized)

The output is structured for efficient LLM consumption:

```
[搜索: activitywatch]
  今天 14:32 [clipboard] VSCode: ActivityWatch is an open-source time-tracking tool...
  昨天 10:15 [manual] Terminal: Read ActivityWatch docs on GH

[最近剪贴板]
  今天 16:45 Safari: "ActivityWatch API reference"
  今天 15:20 VSCode: "def init_watcher():"

[今天时间线]
  14:00-14:30 Safari (18m) - window: ActivityWatch GitHub
  14:30-16:45 VSCode (2h 15m) - window: keypulse/watchers.py
  16:45-17:00 Safari (15m) - window: ActivityWatch Issues

[手动保存]
  今天 16:00 "Need to integrate FTS5 indexing for clipboard"
```

### Command options

```bash
keypulse recall <query> [OPTIONS]

Options:
  --since TIMESPEC        Search time range (default: 7d)
                         Examples: 7d, 24h, 2w, today, yesterday
  --limit N              Max results to return (default: 5)
  --help                 Show help message
```

## ⚠️ Important Notes

### Requires daemon running

The skill only works if `keypulse start` is already running in the background. If the daemon stops, you'll see an error:

```
[错误] KeyPulse 未运行，请先执行: keypulse start
```

Start it again:

```bash
keypulse start
keypulse status
```

### Local-only execution

All data stays on your machine:
- The skill runs locally; no data is sent to Claude/OpenClaw servers
- Activity history never leaves your computer
- No network communication for skill queries

### Privacy-first by design

Sensitive apps are automatically excluded from recall:
- 1Password, Keychain, LastPass, Bitwarden — never recorded
- Slack, Teams, Discord — app names only, no content
- Passwords and tokens — automatically masked
- Custom rules via `~/.keypulse/config.toml`

## 🎯 Skill Capabilities

### What the skill CAN do

✅ Search clipboard history by keyword
✅ Browse session timeline (today, this week, past days)
✅ Retrieve manual notes you saved with `keypulse save`
✅ Answer "what was I doing X days ago?"
✅ Find related activities by keyword or app
✅ Provide natural language summaries of your work
✅ Help context-switch back to previous projects

### What the skill DOES NOT do

❌ Auto-summarize sessions (that's up to Claude's synthesis)
❌ Generate reports (use `keypulse stats` for that)
❌ Access browser history or document contents (only what you've copied)
❌ Export or share data outside your machine
❌ Predict future activity or provide recommendations

## 📊 Differences from CLI

| Feature | CLI (`keypulse search`) | Skill (`/work-recall`) |
|---------|----------------------|---------------------|
| Output format | Rich tables, pretty printing | Plain text, token-optimized |
| Default limit | 50 results | 5 (compact for LLM context) |
| Time range | Configurable via --since | Defaults to last 7 days |
| Filtering | By app, source, date | By query keyword only |
| Purpose | Exploration, reporting | LLM-assisted work recall |
| Automation | Manual CLI commands | Auto-triggered by questions |

### CLI example:

```bash
keypulse search "python" --since 30d --app VSCode --limit 50
```

### Skill equivalent:

```
/work-recall python development work I did
```

## ⚙️ Configuration

Both the CLI and skill use the same configuration file at `~/.keypulse/config.toml`.

### Key settings affecting the skill

```toml
[clipboard]
max_text_length = 2000      # Max clipboard length indexed
dedup_window_sec = 600      # Dedup identical copies within 10 min

[privacy]
redact_emails = true        # Mask email addresses in results
redact_tokens = true        # Mask API keys and tokens
redact_phones = true        # Mask phone numbers

[app]
retention_days = 30         # How far back history goes
flush_interval_sec = 5      # How often data is saved
```

### Disable the skill temporarily

Pause the daemon without stopping it:

```bash
keypulse pause
```

Resume later:

```bash
keypulse resume
```

Or stop the daemon entirely:

```bash
keypulse stop
```

## 🔍 Troubleshooting

### "KeyPulse not running" error

**Error message:**
```
[错误] KeyPulse 未运行，请先执行: keypulse start
```

**Solution:**

```bash
keypulse start
keypulse status
```

Check if there were any startup issues:

```bash
tail -f ~/.keypulse/keypulse.log
```

### Skill not appearing in Claude Code

The skill will appear when:
1. Claude Code recognizes this repository (opened as a project)
2. The `.claude/skills/work-recall/SKILL.md` file exists in the repo
3. You're using an up-to-date version of Claude Code

If it still doesn't appear:
- Refresh the project view
- Restart Claude Code
- Verify the file exists: `ls -la .claude/skills/work-recall/SKILL.md`

### No results from search

If `/work-recall <query>` returns no results:

**Check that data is being recorded:**

```bash
keypulse timeline --today
keypulse recent --type clipboard
```

If you see output, the daemon is working but hasn't recorded anything matching your query yet. Give it time to accumulate data.

**Check privacy rules:**

Make sure the apps you're using aren't blacklisted:

```bash
keypulse config show
```

Look at the privacy section and any explicit rules.

### Poor search results

If the skill returns irrelevant results:

1. **Be more specific** in your question:
   - Bad: `/work-recall python`
   - Good: `/work-recall python async performance optimization`

2. **Check the clipboard** was actually captured:
   ```bash
   keypulse recent --type clipboard --limit 20
   ```

3. **Search with the CLI** for comparison:
   ```bash
   keypulse search "your query" --since 7d
   ```

4. **Review recent activity:**
   ```bash
   keypulse timeline --today
   ```

### Daemon keeps stopping

If `keypulse status` shows "not running" when you expect it to be:

1. Check the log for errors:
   ```bash
   tail -50 ~/.keypulse/keypulse.log
   ```

2. Restart with verbose logging:
   ```bash
   keypulse stop
   keypulse start
   ```

3. Verify system permissions (macOS):
   ```bash
   System Settings → Privacy & Security → Accessibility
   System Settings → Privacy & Security → Screen Recording
   ```

4. Check for competing instances:
   ```bash
   pgrep -f "keypulse" | wc -l
   ```

   If more than one, manually kill extras:
   ```bash
   pkill -f "keypulse"
   keypulse start
   ```

## 🧠 Using the skill effectively

### Ask natural questions

The skill is designed for conversational work-recall:

```
✅ "What was I researching yesterday afternoon?"
✅ "Show me the code I copied about async/await"
✅ "What project did I work on 3 days ago?"
✅ "Help me remember what I was debugging last week"

❌ "latest search results" (too vague)
❌ "all clipboard entries" (too broad)
❌ "predictive suggestions" (out of scope)
```

### Use for context switching

When returning to a project:

```
/work-recall what was I working on with the authentication system last week?
```

Claude will synthesize your recent activity to help you get back up to speed.

### Combine with Claude's other capabilities

The skill works alongside Claude's other features:

```
/work-recall X component implementation details

Now, can you review my latest code and suggest improvements based on what I was working on?
```

Claude can now read both your activity history and your code together.

### Use the CLI for deeper analysis

For detailed exploration, use the CLI directly:

```bash
keypulse timeline --date 2026-04-10
keypulse stats --days 7
keypulse export --format json --days 30 | jq . > my_activity.json
```

## 🔗 Related

### Within this repository

- **Main CLI Branch** — Full command reference and architecture docs: [`claude/review-spec-repo-6jybR`](https://github.com/Longfellow1/keypulse/tree/claude/review-spec-repo-6jybR)
- **Architecture** — See [README on main branch](https://github.com/Longfellow1/keypulse/blob/claude/review-spec-repo-6jybR/README.md) for detailed system design
- **Privacy Policy** — [SECURITY.md](SECURITY.md)
- **Contributing** — [CONTRIBUTING.md](CONTRIBUTING.md)

### External resources

- **GitHub Repository** — [github.com/Longfellow1/keypulse](https://github.com/Longfellow1/keypulse)
- **License** — MIT, see [LICENSE](LICENSE)

## FAQ

### Does the skill upload my data anywhere?

No. All queries run locally. Your activity history never leaves your machine. The skill invokes `keypulse recall` which reads from your local database and returns plain text.

### Can I share this skill with others?

The skill itself is open source (MIT license), but it only works with a personal KeyPulse daemon. Each user needs to:
1. Install KeyPulse
2. Start their own daemon
3. Set up the skill in their Claude Code / OpenClaw

The skill can't access other people's activity data; it's isolated to your machine.

### What if I have privacy concerns?

KeyPulse is designed for privacy:
- Open source — audit the code anytime
- Local-first — no uploads or tracking
- Configurable — control exactly what gets recorded
- Maskable — sensitive data is auto-detected and redacted

Review the privacy settings in `~/.keypulse/config.toml` and the Privacy & Security section of the [main README](https://github.com/Longfellow1/keypulse/tree/claude/review-spec-repo-6jybR).

### How much data does it store?

By default, 30 days of activity (configurable). Typical disk usage:
- Light usage: ~600 KB/week
- Normal workday: ~1.2 MB/week
- Heavy usage: ~2 MB/week

The database is stored at `~/.keypulse/keypulse.db`.

### Can I use the skill offline?

Yes, the skill works entirely offline once the daemon is running. All data is local, and there's no network requirement for skill queries.

### What happens if the daemon crashes?

The skill will return an error:
```
[错误] KeyPulse 未运行，请先执行: keypulse start
```

Restart it:
```bash
keypulse start
```

Your data is preserved in the SQLite database.

## Examples

### Example 1: Finding yesterday's research

**Question:**
```
/work-recall What did I research about authentication yesterday?
```

**Claude synthesizes and responds:**
```
Yesterday around 14:30-16:00, you were researching authentication in Safari 
and implementing related code in VSCode. You copied several resources:

- "OAuth 2.0 specification" (14:45)
- Code snippet from passport.js authentication example (15:20)
- Your manual note: "Implement JWT refresh token strategy" (15:45)

You spent most of that time in VSCode (1h 30m), suggesting you were actively 
coding while referencing the research material.
```

### Example 2: Returning to a project

**Question:**
```
/work-recall Help me pick up where I left off with the database refactoring
```

**Claude synthesizes and responds:**
```
Your last work on database refactoring was 3 days ago (April 12). You spent 
2 hours in VSCode on the keypulse/store/schema.py file, and your notes mention:

- "Add migration scripts for FTS5 schema changes"
- "Batch write optimization — test at 5s flush intervals"

You also copied several SQL patterns related to transaction handling. 
Today might be a good time to check your most recent commits and any 
TODOs you left in the code.
```

### Example 3: App usage patterns

**Question:**
```
/work-recall How much time did I spend on communication vs coding this week?
```

**Claude synthesizes and responds:**
```
Based on your recent activity this week:

**Coding/Development (VSCode, Terminal): ~18 hours**
- Mostly focused work with low idle time
- Frequent context switches between files

**Communication (Slack, Mail): ~4 hours**
- Spike on Wednesday afternoon (team sync)
- Usually 15-30 minute blocks

**Research (Safari, docs): ~6 hours**
- ActivityWatch architecture review
- Performance optimization research

You maintained good focus overall with about 70% deep work time.
```

---

**Last updated:** April 15, 2026

**Note:** This skill requires Python 3.11+, macOS 12.0+, and the KeyPulse daemon to be running. See [SKILL_SETUP.md](SKILL_SETUP.md) for detailed installation and troubleshooting.
