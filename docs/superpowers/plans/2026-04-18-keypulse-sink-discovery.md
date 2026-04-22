# KeyPulse Sink Discovery and Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect the best local knowledge sink for KeyPulse, bind to Obsidian when a vault is present, and fall back to standalone markdown output when no sink is available.

**Architecture:** Add a small sink registry that resolves one active output target from explicit overrides, persisted bindings, and filesystem discovery. Keep the exporter file-based and deterministic so the sink layer stays thin and can later be split into independent open-source adapters. The user-facing behavior remains simple: install once, run forever, and quietly route exports to the right local target.

**Tech Stack:** Python 3.13, Click, Pydantic, TOML/JSON state files, pytest, macOS filesystem conventions.

---

### Task 1: Add sink discovery primitives and tests

**Files:**
- Create: `keypulse/integrations/__init__.py`
- Create: `keypulse/integrations/sinks.py`
- Create: `keypulse/integrations/state.py`
- Create: `tests/test_sink_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from keypulse.integrations.sinks import resolve_active_sink


def test_resolve_active_sink_prefers_obsidian_vault(tmp_path: Path):
    home = tmp_path
    vault = home / "Go" / "Knowledge"
    (vault / ".obsidian").mkdir(parents=True)
    (home / "Library" / "Application Support" / "obsidian").mkdir(parents=True, exist_ok=True)
    (home / "Library" / "Application Support" / "obsidian" / "obsidian.json").write_text(
        '{"vaults":{"abc":{"open":true,"path":"%s"}}}' % vault
    )

    sink = resolve_active_sink(home=home)

    assert sink.kind == "obsidian"
    assert sink.output_dir == vault
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sink_discovery.py::test_resolve_active_sink_prefers_obsidian_vault -v`
Expected: FAIL because `keypulse.integrations.sinks` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SinkTarget:
    kind: str
    output_dir: Path
    source: str


def resolve_active_sink(home: Path | None = None) -> SinkTarget:
    home = home or Path.home()
    obsidian_json = home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if obsidian_json.exists():
        return SinkTarget(kind="obsidian", output_dir=home / "Go" / "Knowledge", source="filesystem")
    return SinkTarget(kind="standalone", output_dir=home / "Go" / "Knowledge", source="fallback")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sink_discovery.py::test_resolve_active_sink_prefers_obsidian_vault -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/integrations tests/test_sink_discovery.py
git commit -m "feat: add sink discovery primitives"
```

### Task 2: Persist the chosen sink and wire export routing through it

**Files:**
- Modify: `keypulse/config.py`
- Modify: `keypulse/services/export.py`
- Modify: `keypulse/cli.py`
- Modify: `tests/test_obsidian_sync_cli.py`
- Create: `tests/test_sink_state.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from keypulse.integrations.state import read_sink_state, write_sink_state
from keypulse.integrations.sinks import SinkTarget


def test_sink_state_round_trip(tmp_path: Path):
    state_file = tmp_path / "sink-state.json"
    target = SinkTarget(kind="obsidian", output_dir=tmp_path / "Knowledge", source="filesystem")

    write_sink_state(state_file, target)
    loaded = read_sink_state(state_file)

    assert loaded.kind == "obsidian"
    assert loaded.output_dir == target.output_dir
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sink_state.py::test_sink_state_round_trip -v`
Expected: FAIL because persistence helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
import json
from dataclasses import asdict


def write_sink_state(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(target), indent=2, default=str))


def read_sink_state(path):
    data = json.loads(path.read_text())
    return SinkTarget(kind=data["kind"], output_dir=Path(data["output_dir"]), source=data["source"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sink_state.py::test_sink_state_round_trip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/config.py keypulse/services/export.py keypulse/cli.py keypulse/integrations tests/test_sink_state.py tests/test_obsidian_sync_cli.py
git commit -m "feat: route exports through sink binding"
```

### Task 3: Expose sink discovery in the CLI and installer

**Files:**
- Modify: `keypulse/cli.py`
- Modify: `install.sh`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-18-keypulse-obsidian-product-spec.md`
- Create: `tests/test_sinks_cli.py`

- [ ] **Step 1: Write the failing test**

```python
from click.testing import CliRunner

from keypulse.cli import main


def test_sinks_detect_reports_binding(monkeypatch):
    monkeypatch.setattr("keypulse.cli.resolve_active_sink", lambda **kwargs: type("Sink", (), {
        "kind": "obsidian",
        "output_dir": "/tmp/Knowledge",
        "source": "filesystem",
    })())

    result = CliRunner().invoke(main, ["sinks", "detect"])

    assert result.exit_code == 0
    assert "obsidian" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sinks_cli.py::test_sinks_detect_reports_binding -v`
Expected: FAIL because the `sinks` command group does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
@main.group()
def sinks():
    """Manage sink discovery and binding."""


@sinks.command("detect")
def sinks_detect():
    sink = resolve_active_sink()
    console.print(f"{sink.kind} -> {sink.output_dir}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sinks_cli.py::test_sinks_detect_reports_binding -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add keypulse/cli.py install.sh README.md docs/superpowers/specs/2026-04-18-keypulse-obsidian-product-spec.md tests/test_sinks_cli.py
git commit -m "feat: auto-detect local sink targets"
```

### Task 4: Verify the end-to-end install and export flow

**Files:**
- Verify: `install.sh`
- Verify: `keypulse/cli.py`
- Verify: `tests/test_sink_discovery.py`
- Verify: `tests/test_sinks_cli.py`

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 2: Run the installer in no-launchd mode**

Run: `bash install.sh --no-launchd`
Expected: KeyPulse installs, creates the local config, and detects a sink without failing when Obsidian is absent.

- [ ] **Step 3: Exercise the new CLI**

Run: `keypulse sinks detect && keypulse obsidian sync --yesterday`
Expected: The CLI prints the resolved sink and writes markdown notes into the detected vault or standalone directory.

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "feat: add auto-detected sink routing"
```
