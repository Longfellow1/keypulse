# KeyPulse — Testing Guide

> Platform: macOS 12.0+  
> Runtime: Python 3.11+

---

## 1. Environment Setup

```bash
git clone https://github.com/longfellow1/keypulse
cd keypulse
pip install -e .
```

Verify installation:

```bash
keypulse --help
```

---

## 2. Pre-flight Check

Run the doctor command to verify dependencies and permissions:

```bash
keypulse doctor
```

Expected output:

```
Python >= 3.11          OK
pyobjc-framework-AppKit OK
pyobjc-framework-Quartz OK
Accessibility permission OK   ← must be OK for window watcher
DB path writable        OK
```

**If Accessibility shows FAIL:**  
System Settings → Privacy & Security → Accessibility → add your terminal app.

---

## 3. Daemon Lifecycle

### Start

```bash
keypulse start
# Expected: KeyPulse started (PID 12345)
```

### Status

```bash
keypulse status
# Expected: running=yes, enabled_watchers=window,idle,clipboard,manual
```

### Pause / Resume

```bash
keypulse pause
keypulse status   # status=paused
keypulse resume
keypulse status   # status=running
```

### Stop

```bash
keypulse stop
# Expected: KeyPulse stopped.
keypulse status   # running=no
```

---

## 4. Data Capture — Manual Verification

Start the daemon, switch between a few apps, then:

```bash
keypulse timeline --today
```

Expected: table of sessions showing app names, window titles, duration.

### Clipboard capture

Copy some text in any app, wait 2 seconds, then:

```bash
keypulse recent --type clipboard --limit 5
```

Expected: your copied text appears (possibly desensitised if it matched privacy patterns).

### Manual save

```bash
keypulse save --text "Test note for keypulse" --tag test
keypulse recent --type manual
```

Expected: your note appears.

Via stdin:

```bash
echo "stdin note" | keypulse save --tag test
```

---

## 5. Search

```bash
keypulse search "keypulse"
keypulse search "keypulse" --app Terminal
keypulse search "keypulse" --since 1d
keypulse search "keypulse" --source manual
```

Expected: results table with Time / Type / App / Content columns.

Plain output (for scripting / skill use):

```bash
keypulse search "keypulse" --plain
# TSV: timestamp\ttype\tapp\ttitle\tbody
```

---

## 6. Recall Command (LLM-optimised)

```bash
keypulse recall "keypulse"
```

Expected: compact multi-section output:

```
[搜索: keypulse]
  今天 14:32 [manual] Terminal: Test note for keypulse

[最近剪贴板]
  今天 14:28 Terminal: ...

[今日主要活动]
  今天 09:00 Terminal (5min): keypulse — main.py

[手动保存]
  今天 14:32 #test: Test note for keypulse
```

---

## 7. Stats & Export

```bash
keypulse stats --days 7
keypulse stats --days 7 --plain
```

```bash
keypulse export --format json --days 1
keypulse export --format csv  --days 1
keypulse export --format md   --date $(date +%Y-%m-%d)
keypulse export --format json --output /tmp/kp_export.json --days 7
```

---

## 8. Session Commands

```bash
keypulse session list
keypulse session list --date $(date +%Y-%m-%d)
```

Grab an ID from the output, then:

```bash
keypulse session show <session-id>
```

---

## 9. Privacy & Policy

### Verify desensitisation

Copy a string containing a fake email or token, e.g.:  
`token=sk-abc123XYZ user@example.com`

Then check:

```bash
keypulse recent --type clipboard --limit 1
```

Expected: email and token replaced with `[EMAIL]` / `[API_KEY]` markers.

### Policy rules

```bash
keypulse rules list
keypulse rules add --scope-type app --scope-value "1Password" --mode deny
keypulse rules list   # new rule appears
keypulse rules disable <id>
```

---

## 10. Purge & Retention

```bash
# Dry-run (shows what would be deleted)
keypulse purge --last-hours 1

# Confirm deletion
keypulse purge --last-hours 1 --confirm

# Delete by app
keypulse purge --app Slack --confirm

# Clear everything from today
keypulse purge --today --confirm
```

---

## 11. Config

```bash
keypulse config show
keypulse config path
```

Edit `~/.keypulse/config.toml` to toggle watchers or adjust thresholds, then restart:

```bash
keypulse stop && keypulse start
```

---

## 12. Database Schema Verification

```bash
sqlite3 ~/.keypulse/keypulse.db ".tables"
```

Expected tables:

```
_schema_version   app_state   policies   raw_events
search_docs       search_docs_fts        sessions
```

Check migration version:

```bash
sqlite3 ~/.keypulse/keypulse.db "SELECT * FROM _schema_version;"
# 1|<timestamp>
```

---

## 13. Single Instance Lock

```bash
keypulse start
keypulse start   # Expected: "Already running (PID ...)"
```

Simulate crash recovery:

```bash
# Manually write a fake stale PID
echo "99999" > ~/.keypulse/keypulse.pid
keypulse start   # Should detect stale lock, start normally
```

---

## 14. Resource Usage

After 30 minutes of normal use:

```bash
ps aux | grep keypulse
# CPU should be < 1%, RSS < 60 MB
```

```bash
ls -lh ~/.keypulse/keypulse.db
# Size should be reasonable (< 5 MB for a day of use)
```

---

## Known Limitations (MVP)

| Limitation | Notes |
|-----------|-------|
| macOS only | Linux/Windows watchers not implemented |
| No browser URL capture | Only window title captured for browsers |
| Clipboard: text only | Images/files not indexed |
| Accessibility required | Without it, window titles fall back to app name only |
