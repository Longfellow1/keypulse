from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_macos_extras_list_real_frameworks():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    macos_extras = data["project"]["optional-dependencies"]["macos"]

    required = {
        "pyobjc-framework-Cocoa",
        "pyobjc-framework-AppKit",
        "pyobjc-framework-ApplicationServices",
        "pyobjc-framework-Quartz",
        "pyobjc-framework-Vision",
    }
    names = {dep.split(">=")[0].split("==")[0].strip() for dep in macos_extras}
    missing = required - names
    assert not missing, f"macos extras missing: {missing}"
