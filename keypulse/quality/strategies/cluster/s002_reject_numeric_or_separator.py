import re

from keypulse.quality.base import Strategy, Verdict


class S002RejectNumericOrSeparator(Strategy):
    id = "S002"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject numeric or separator-only text"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        if re.fullmatch(r"[\d\-._]+", text):
            return Verdict(accept=False, reason="numeric-or-separator")
        return Verdict(accept=True)
