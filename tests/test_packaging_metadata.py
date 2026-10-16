from __future__ import annotations

import tomllib
from pathlib import Path

from penpal.cli import legacy_main


def test_pyproject_macos_extras_list_real_frameworks():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    macos_extras = data["project"]["optional-dependencies"]["macos"]

    # NB: AppKit module is shipped inside pyobjc-framework-Cocoa — there is no
    # standalone pyobjc-framework-AppKit wheel on PyPI. Listing it here would
    # break `pip install '.[macos]'`.
    required = {
        "pyobjc-framework-Cocoa",
        "pyobjc-framework-ApplicationServices",
        "pyobjc-framework-Quartz",
        "pyobjc-framework-Vision",
    }
    names = {dep.split(">=")[0].split("==")[0].strip() for dep in macos_extras}
    missing = required - names
    assert not missing, f"macos extras missing: {missing}"


def test_package_metadata_uses_penpal_names_and_keeps_cli_alias(monkeypatch, capsys):
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())

    assert data["project"]["name"] == "penpal-mcp"
    assert data["project"]["scripts"]["penpal"] == "penpal.cli:main"
    assert data["project"]["scripts"]["keypulse"] == "penpal.cli:legacy_main"
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["penpal*"]

    calls = []
    monkeypatch.setattr("penpal.cli.main", lambda: calls.append("main"))
    legacy_main()
    assert calls == ["main"]
    assert capsys.readouterr().err == "Deprecated command: use `penpal` instead of `keypulse`.\n"
