from keypulse.obsidian.layout import slugify
from keypulse.quality.base import Strategy, Verdict


class S003RejectUrlLike(Strategy):
    id = "S003"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject URL-like text"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        lowered = text.lower()
        slug_value = slugify(text, fallback="")
        if lowered.startswith("http") or "://" in lowered or slug_value.startswith(("http-", "https-")):
            return Verdict(accept=False, reason="url-like")
        return Verdict(accept=True)
