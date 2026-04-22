import re

from keypulse.quality.base import Strategy, Verdict


class S005RejectSecretPattern(Strategy):
    id = "S005"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject secret-like patterns"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        lowered = text.lower()
        if re.match(r"^sk[-_]", lowered) or re.search(r"[0-9a-f]{16,}", lowered):
            return Verdict(accept=False, reason="secret-pattern")
        return Verdict(accept=True)
