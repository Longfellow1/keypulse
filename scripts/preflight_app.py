#!/usr/bin/env python3
"""Preflight check for the built .app bundle via capability registry."""
from __future__ import annotations

import sys
from pathlib import Path

from keypulse.capabilities.registry import build_default_registry


def fail(msg: str) -> None:
    print(f"[preflight] FAIL {msg}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: preflight_app.py <path/to/KeyPulse.app>", file=sys.stderr)
        return 2

    app_path = Path(sys.argv[1]).resolve()
    if not app_path.exists():
        fail(f"{app_path} does not exist")
        return 1

    registry = build_default_registry(app_path=app_path)
    results = registry.precheck_all()
    errors: list[str] = []
    for name, result in results:
        if result.ok:
            continue
        message = result.hint or result.code
        errors.append(f"{name}: {message}")

    if errors:
        for err in errors:
            fail(err)
        print(f"[preflight] {len(errors)} issue(s) — install aborted.", file=sys.stderr)
        return 1

    print(f"[preflight] OK {app_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
