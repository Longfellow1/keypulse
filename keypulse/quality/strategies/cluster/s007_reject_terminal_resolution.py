import re

from keypulse.quality.base import Strategy, Verdict


class S007RejectTerminalResolution(Strategy):
    id = "S007"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject terminal resolution strings"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        if re.search(r"-\d+-\d+$", text):
            return Verdict(accept=False, reason="terminal-resolution")
        return Verdict(accept=True)
