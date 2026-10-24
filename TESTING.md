# Penpal — Testing Guide

> Platform: macOS 12.0+  
> Runtime: Python 3.11+

---

## 1. Environment Setup

```bash
git clone https://github.com/longfellow1/penpal
cd penpal
pip install -e .
```

Verify installation:

```bash
penpal --help
```

---

## 2. Pre-flight Check

Run the doctor command to verify dependencies and permissions:

```bash
penpal doctor
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
penpal start
# Expected: Penpal started (PID 12345)
```

### Status

```bash
penpal status
# Expected: running=yes, enabled_watchers=window,idle,clipboard,manual
```

### Pause / Resume

```bash
penpal pause
penpal status   # status=paused
penpal resume
penpal status   # status=running
```

### Stop

```bash
penpal stop
# Expected: Penpal stopped.
penpal status   # running=no
```

---

## 4. Data Capture — Manual Verification

Start the daemon, switch between a few apps, then:

```bash
penpal timeline --today
```

Expected: table of sessions showing app names, window titles, duration.

### Clipboard capture

Copy some text in any app, wait 2 seconds, then:

```bash
penpal recent --type clipboard --limit 5
```

Expected: your copied text appears (possibly desensitised if it matched privacy patterns).

### Manual save

```bash
penpal save --text "Test note for penpal" --tag test
penpal recent --type manual
```

Expected: your note appears.

Via stdin:

```bash
echo "stdin note" | penpal save --tag test
```

---

## 5. Search

```bash
penpal search "penpal"
penpal search "penpal" --app Terminal
penpal search "penpal" --since 1d
penpal search "penpal" --source manual
```

Expected: results table with Time / Type / App / Content columns.

Plain output (for scripting / skill use):

```bash
penpal search "penpal" --plain
# TSV: timestamp\ttype\tapp\ttitle\tbody
```

---

## 6. Stats & Export

```bash
penpal stats --days 7
penpal stats --days 7 --plain
```

```bash
penpal export --format json --days 1
penpal export --format csv  --days 1
penpal export --format md   --date $(date +%Y-%m-%d)
penpal export --format json --output /tmp/kp_export.json --days 7
```

---

## 7. Session Commands

```bash
penpal session list
penpal session list --date $(date +%Y-%m-%d)
```

Grab an ID from the output, then:

```bash
penpal session show <session-id>
```

---

## 8. Privacy & Policy

### Verify desensitisation

Copy a string containing a fake email or token, e.g.:  
`token=sk-abc123XYZ user@example.com`

Then check:

```bash
penpal recent --type clipboard --limit 1
```

Expected: email and token replaced with `[EMAIL]` / `[API_KEY]` markers.

### Policy rules

```bash
penpal rules list
penpal rules add --scope-type app --scope-value "1Password" --mode deny
penpal rules list   # new rule appears
penpal rules disable <id>
```

---

## 9. Purge & Retention

```bash
# Dry-run (shows what would be deleted)
penpal purge --last-hours 1

# Confirm deletion
penpal purge --last-hours 1 --confirm

# Delete by app
penpal purge --app Slack --confirm

# Clear everything from today
penpal purge --today --confirm
```

---

## 10. Config

```bash
penpal config show
penpal config path
```

Edit `~/.penpal/config.toml` to toggle watchers or adjust thresholds, then restart:

```bash
penpal stop && penpal start
```

---

## 12. Single Instance Lock

```bash
penpal start
penpal start   # Expected: "Already running (PID ...)"
```

Simulate crash recovery:

```bash
# Manually write a fake stale PID
echo "99999" > ~/.penpal/penpal.pid
penpal start   # Should detect stale lock, start normally
```

---

## 13. Resource Usage

After 30 minutes of normal use:

```bash
ps aux | grep penpal
# CPU should be < 1%, RSS < 60 MB
```

```bash
ls -lh ~/.penpal/penpal.db
# Size should be reasonable (< 5 MB for a day of use)
```

## 14. Automated Tests and Opt-in Checks

The default pytest run uses synthetic fixtures and injected system probes. Performance benchmarks and
tests that call real macOS system commands are skipped unless explicitly enabled:

```bash
# Large synthetic migration and search timing checks
PENPAL_RUN_SLOW=1 python -m pytest -q -m slow

# Real Spotlight (mdimport / mdfind) and ioreg smoke checks on macOS
PENPAL_RUN_SYSTEM_SMOKE=1 python -m pytest -q -m system_smoke
```

The system smoke checks use temporary Spotlight files. The ioreg smoke check reads the current
machine's idle-time value and should only be run as an intentional local check.

---

## Known Limitations (MVP)

| Limitation | Notes |
|-----------|-------|
| macOS only | Linux/Windows watchers not implemented |
| Clipboard: text only | Images/files not indexed |
| Accessibility required | Without it, window titles fall back to app name only |
