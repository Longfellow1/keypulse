from keypulse.quality.base import Strategy, Verdict


class S006RejectCommandFlag(Strategy):
    id = "S006"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject command flag-like text"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        if "--" in text:
            return Verdict(accept=False, reason="command-flag")
        return Verdict(accept=True)
