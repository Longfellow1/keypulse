from __future__ import annotations

import subprocess
from pathlib import Path


def test_makefile_paths_are_based_on_makefile_location(tmp_path: Path) -> None:
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    repo_root = makefile.parent

    result = subprocess.run(
        ["make", "-pn", "-f", str(makefile)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"ROOT := {repo_root}" in result.stdout
    assert f"PYTHON := {repo_root}/.venv/bin/python" in result.stdout
