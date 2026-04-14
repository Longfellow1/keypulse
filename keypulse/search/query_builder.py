from __future__ import annotations
import re


def build_fts_query(raw: str) -> str:
    """
    Convert a user query string to an FTS5 query.
    - Multi-word queries become: word1 word2 (AND by default in FTS5)
    - Quoted phrases pass through: "exact phrase"
    - Special chars are escaped to avoid FTS5 syntax errors
    """
    raw = raw.strip()
    if not raw:
        return '""'

    # If already has quotes (phrase search), pass through with basic sanitization
    if '"' in raw:
        # Sanitize: allow only word chars, spaces, and quotes
        safe = re.sub(r'[^\w\s"\'*-]', ' ', raw)
        return safe.strip() or '""'

    # Split on whitespace, escape each term
    terms = raw.split()
    escaped = []
    for t in terms:
        # FTS5 special chars: " * ^ { }
        t_clean = re.sub(r'["\^{}]', '', t)
        if t_clean:
            escaped.append(t_clean)

    return " ".join(escaped) if escaped else '""'
