import re

from keypulse.quality.base import Strategy, Verdict


class S008RejectInsufficientContent(Strategy):
    id = "S008"
    version = "0.1.0"
    layer = "cluster"
    description = "Reject insufficient content"

    def apply(self, value: str, context: dict | None = None) -> Verdict:
        text = str(value or "").strip()
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        english_words = len(re.findall(r"[A-Za-z]+", text))
        if chinese_chars < 2 and english_words < 3:
            return Verdict(accept=False, reason="insufficient-content")
        return Verdict(accept=True)
