#!/usr/bin/env python3

import locale
import os
import sys
from pathlib import Path


def _force_utf8_locale() -> None:
    """py2app's embedded Python ignores LANG/LC_ALL/PYTHONUTF8 env vars at
    boot, leaving locale.getpreferredencoding()='US-ASCII'. This breaks any
    `Path.read_text()` / `open()` without explicit encoding when files contain
    non-ASCII bytes (Chinese topic names, anchors, etc.). Set locale before
    any keypulse imports happen.
    """
    for candidate in ("en_US.UTF-8", "C.UTF-8", "UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, candidate)
            return
        except locale.Error:
            continue


_force_utf8_locale()


def _load_secrets_env() -> None:
    """Load ~/.keypulse/secrets.env into os.environ.

    Why: the .app is launched directly by launchd (no shell wrapper), so
    KEY=value lines need to be parsed in-process before keypulse.cli imports
    anything that reads them.
    """
    path = Path.home() / ".keypulse" / "secrets.env"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_secrets_env()

from keypulse.cli import main  # noqa: E402


if __name__ == "__main__":
    # GUI launch (Finder/Dock double-click) lands here with no argv.
    # Route to `hud start` so the .app behaves as a menu-bar app per
    # Info.plist LSUIElement=true; otherwise click would print --help
    # and exit 1, which LaunchServices reports as a py2app launch error.
    if len(sys.argv) == 1:
        sys.argv.extend(["hud", "start"])
    sys.exit(main())
