from keypulse.quality.base import Strategy, Verdict


class S001RejectEmpty(Strategy):
    id = "S001"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject empty text"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "")
        if not text.strip():
            return Verdict(accept=False, reason="empty")
        return Verdict(accept=True)
