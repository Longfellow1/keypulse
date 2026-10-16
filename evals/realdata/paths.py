"""Where eval data lives. Never inside the git repository."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DATA_DIR = Path("~/.penpal/evals")
REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_data_dir(path: str | Path | None) -> Path:
    p = Path(path or DEFAULT_DATA_DIR).expanduser().resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to use a data dir inside the repository: {p}")
    if _inside_git_worktree(p):
        raise SystemExit(f"refusing to use a data dir inside a git work tree: {p}")
    return p


def _inside_git_worktree(p: Path) -> bool:
    probe = p
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        r = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def new_run_dir(data_dir: Path) -> Path:
    runs = data_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    d = runs / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    d.mkdir()
    return d


def latest_run_dir(data_dir: Path) -> Path:
    runs_dir = data_dir / "runs"
    runs = sorted(p for p in runs_dir.glob("*") if p.is_dir()) if runs_dir.is_dir() else []
    if not runs:
        raise SystemExit(f"no runs under {runs_dir}; run `prepare` first")
    return runs[-1]
