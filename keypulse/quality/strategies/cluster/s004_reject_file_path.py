from keypulse.obsidian.layout import slugify
from keypulse.quality.base import Strategy, Verdict


class S004RejectFilePath(Strategy):
    id = "S004"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject file path-like text"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        lowered = text.lower()
        slug_value = slugify(text, fallback="")
        if text.startswith("/") or slug_value.startswith(("users-", "library-", "opt-")):
            return Verdict(accept=False, reason="file-path")
        if any(ext in lowered for ext in (".md", ".py", ".rar", ".xlsx", ".pptx")):
            return Verdict(accept=False, reason="file-path")
        if slug_value.endswith(("-md", "-py", "-rar", "-xlsx", "-pptx")):
            return Verdict(accept=False, reason="file-path")
        return Verdict(accept=True)
