# KeyPulse + Obsidian Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make KeyPulse installable and runnable on this Mac, then add a local-first pipeline that turns captured activity into Obsidian-ready daily notes, event cards, and theme cards.

**Architecture:** KeyPulse remains the low-level activity capture daemon and SQLite event store. Obsidian becomes the presentation and knowledge layer through a new local export bridge that converts activity events into markdown notes with properties, backlinks, and folder conventions. The first release is fully local, deterministic, and file-based: capture -> export -> Obsidian vault.

**Tech Stack:** Python 3.13, Click, SQLite/FTS5, PyObjC, Markdown, Obsidian core plugins (`Properties`, `Bases`, `Daily notes`, `Templates`), pytest.

---

### Task 1: Fix packaging and installer so KeyPulse actually installs on this machine

**Files:**
- Modify: `pyproject.toml`
- Modify: `install.sh`
- Modify: `README.md`
- Create: `tests/test_install_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

```python
import subprocess
from pathlib import Path


def test_keypulse_help_runs_from_repo_root():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["python3", "-m", "keypulse.cli", "--help"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "KeyPulse" in result.stdout
```

- [ ] **Step 2: Run the smoke test and confirm the current failure**

Run: `pytest tests/test_install_smoke.py -v`

Expected: fail before packaging fixes because the local environment cannot install the macOS bridge dependencies cleanly.

- [ ] **Step 3: Update packaging metadata to match the real PyObjC dependency set**

Replace the dependency block in `pyproject.toml` with:

```toml
[project]
name = "keypulse"
version = "0.1.0"
description = "Local-first personal activity memory layer for macOS"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "rich>=13.7",
    "pydantic>=2.5",
    "pydantic-settings>=2.0",
    "pyobjc-framework-Cocoa>=12.0",
    "pyobjc-framework-ApplicationServices>=12.0",
    "pyobjc-framework-Quartz>=12.0",
]

[project.scripts]
keypulse = "keypulse.cli:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["keypulse*"]
```

Replace the installer dependency probe in `install.sh` with a single explicit bootstrap path that:
1. Uses `python3.13` if available, then `python3.12`, then `python3.11`.
2. Forces `PIP_USER=false` before any pip invocation.
3. Installs the package with `"$VENV_PIP" install --no-user -e "$REPO_DIR"` in dev mode or `"$VENV_PIP" install --no-user "$REPO_DIR"` in normal mode.
4. Checks `keypulse --help` and `keypulse doctor` before writing the launchd plist.

Update the Quick Start in `README.md` so the only supported local setup path is the installer, not bare `pip install -e .`.

- [ ] **Step 4: Run the installer locally**

Run: `bash install.sh --no-launchd`

Expected: the venv installs successfully, `~/.local/bin/keypulse` is created, and `~/.keypulse/config.toml` is written.

- [ ] **Step 5: Verify the command works**

Run:
```bash
~/.local/bin/keypulse --help
~/.local/bin/keypulse doctor
```

Expected: both commands exit `0`.

- [ ] **Step 6: Commit the packaging fix**

```bash
git add pyproject.toml install.sh README.md tests/test_install_smoke.py
git commit -m "fix: make keypulse installable on macOS"
```

### Task 2: Add a local Obsidian export bridge that converts captured activity into notes

**Files:**
- Create: `keypulse/obsidian/__init__.py`
- Create: `keypulse/obsidian/model.py`
- Create: `keypulse/obsidian/exporter.py`
- Create: `keypulse/obsidian/layout.py`
- Modify: `keypulse/services/export.py`
- Modify: `keypulse/cli.py`
- Create: `tests/test_obsidian_exporter.py`

- [ ] **Step 1: Write the failing transformation test**

```python
from keypulse.obsidian.exporter import build_obsidian_bundle


def test_build_obsidian_bundle_groups_events_into_day_and_topic_cards():
    bundle = build_obsidian_bundle(
        [
            {
                "created_at": "2026-04-18T09:00:00+00:00",
                "source": "manual",
                "ref_type": "manual",
                "title": "修复 keypulse 安装问题",
                "body": "pyobjc dependency and PIP_USER conflict",
                "app_name": "Terminal",
                "tags": "keypulse,install",
            }
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-18",
    )

    assert "daily" in bundle
    assert "topics" in bundle
    assert bundle["daily"][0]["properties"]["source"] == "keypulse"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/test_obsidian_exporter.py -v`

Expected: fail because the Obsidian bridge does not exist yet.

- [ ] **Step 3: Implement the exporter with a strict, local-only markdown contract**

Create an exporter that converts `search_docs` / `sessions` / manual notes into three output types:
1. `daily` notes for that day
2. `event` cards for minimal archivable fragments
3. `topic` cards for grouped themes

Use this note shape:

```python
{
    "path": "Daily/2026-04-18.md",
    "properties": {
        "type": "daily",
        "source": "keypulse",
        "date": "2026-04-18",
        "vault": "Harland Knowledge",
    },
    "body": "markdown content here"
}
```

The exporter must:
1. Keep raw capture out of the note body unless it is already redacted or summarized.
2. Promote repeated patterns into topic cards.
3. Preserve evidence references back to the source event.
4. Write deterministic markdown so repeated exports do not churn the vault.

Expose the functionality through `keypulse export --format obsidian --output <dir>` while preserving existing `json`, `csv`, and `md` formats.

- [ ] **Step 4: Run the exporter test**

Run: `pytest tests/test_obsidian_exporter.py -v`

Expected: pass once the bridge and CLI hook are implemented.

- [ ] **Step 5: Verify an Obsidian export writes files**

Run:
```bash
mkdir -p /tmp/keypulse-obsidian
~/.local/bin/keypulse export --format obsidian --output /tmp/keypulse-obsidian
find /tmp/keypulse-obsidian -maxdepth 2 -type f | sort
```

Expected: markdown notes appear in a stable folder layout.

- [ ] **Step 6: Commit the Obsidian bridge**

```bash
git add keypulse/obsidian keypulse/services/export.py keypulse/cli.py tests/test_obsidian_exporter.py
git commit -m "feat: add obsidian export bridge"
```

### Task 3: Lock in the vault layout, docs, and operator workflow

**Files:**
- Create: `docs/superpowers/specs/2026-04-18-keypulse-obsidian-product-spec.md`
- Create: `integrations/obsidian/README.md`
- Create: `integrations/obsidian/templates/daily-note.md`
- Create: `integrations/obsidian/templates/event-card.md`
- Create: `integrations/obsidian/templates/topic-card.md`
- Create: `integrations/obsidian/vault-layout.md`
- Modify: `README.md`

- [ ] **Step 1: Write the vault-layout doc**

```markdown
# Vault Layout

- `Inbox/` for raw imports awaiting review
- `Daily/` for generated daily notes
- `Events/` for atomic archivable fragments
- `Topics/` for grouped thinking and method notes
- `Sources/` for clipped external references
- `Archive/` for retired material
```

- [ ] **Step 2: Write the operator README**

Document the operating loop:
1. KeyPulse captures local activity.
2. The exporter writes markdown into the vault.
3. Obsidian `Bases` surfaces daily notes, open questions, and topic cards.
4. Weekly review promotes stable patterns into evergreen topics.

Include a concrete mapping table for:
1. `event -> event card`
2. `manual note -> event card`
3. `repeated events -> topic card`
4. `daily rollup -> daily note`

- [ ] **Step 3: Update the top-level README**

Add a short section that explains:
1. KeyPulse is the capture layer.
2. Obsidian is the knowledge surface.
3. The new export command is the supported bridge between them.

- [ ] **Step 4: Final verification**

Run:
```bash
pytest
python3 -m keypulse.cli doctor
python3 -m keypulse.cli export --format obsidian --output /tmp/keypulse-obsidian
```

Expected: tests pass, the daemon-related CLI loads, and Obsidian notes export successfully.

- [ ] **Step 5: Commit the docs and layout**

```bash
git add docs/superpowers/specs/2026-04-18-keypulse-obsidian-product-spec.md integrations/obsidian README.md
git commit -m "docs: define keypulse obsidian operating model"
```

## Self-Review Checklist

- [ ] The packaging task fixes the actual failure mode seen on this machine: `pip install` in the venv and missing PyObjC package names.
- [ ] The Obsidian task produces a local-only file bridge instead of a cloud dependency.
- [ ] The vault layout reflects the agreed hierarchy: `思考 -> 方法论 -> 执行经验`, with time ordering as a secondary index only.
- [ ] No task depends on a function or file that is never introduced earlier in the plan.
- [ ] The plan can be executed incrementally and verified after each commit.

