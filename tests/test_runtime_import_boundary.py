from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PR2_LIVENESS_ALLOWLIST: set[str] = set()


def test_runtime_evidence_has_no_duplicate_readers() -> None:
    violations: list[str] = []
    evidence_implementations = {
        "penpal/foundation/runtime_record.py",
        "penpal/foundation/runtime_launchd.py",
    }
    for path in (ROOT / "penpal").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("penpal/runtime/") or relative in evidence_implementations:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?:read_text|read_bytes|json\.load|open)\s*\([^\n]*daemon\.json", source):
            violations.append(f"{relative}: reads daemon.json outside penpal.runtime")
        if any("launchctl" in line and re.search(r"\b(print|list)\b", line) for line in source.splitlines()):
            if relative not in PR2_LIVENESS_ALLOWLIST:
                violations.append(f"{relative}: parses launchctl print/list output outside penpal.runtime")
        if re.search(r"os\.kill\([^,]+,\s*0\s*\)", source):
            if relative not in PR2_LIVENESS_ALLOWLIST:
                violations.append(f"{relative}: uses os.kill(pid, 0) for liveness")
    assert not violations, "\n".join(violations)
