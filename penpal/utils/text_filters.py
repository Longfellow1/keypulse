"""Text filtering utilities for IME composition and noise detection."""

from __future__ import annotations

import re

# Invisible formatting characters that only add noise to stored text and search (D27): zero-width
# space/joiners and direction marks (U+200B-200F), bidi embeddings/overrides (U+202A-202E), word
# joiner and invisible operators (U+2060-2064), BOM (U+FEFF), soft hyphen (U+00AD).
INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")


def strip_invisible(text: str | None) -> str | None:
    """Remove zero-width / bidi / BOM / soft-hyphen characters. None passes through."""
    if not text:
        return text
    return INVISIBLE_CHARS_RE.sub("", text)


_L5_MIN_LEN = 12
_L5_BOUNDARY_CHARS = {" ", "\t", "\n", "\r", "-", "_", ".", ",", ":", ";", "/", "\\", "(", ")", "[", "]", "{", "}"}


def looks_like_ime_composition(sample: str) -> bool:
    """Language-neutral noise filter: detects samples that look like IME intermediate state.

    Returns True if the longest run of characters between word/code boundaries is >= 12 chars
    AND that run looks like a flat lowercase blob (not camelCase identifier).
    Catches IME intermediate state, tokens, hashes, slugs. Skips text with CJK chars.
    """
    if not sample:
        return False
    s = sample.strip()
    if len(s) < _L5_MIN_LEN:
        return False
    # CJK / non-ASCII letters present → real prose in another script, keep
    if any(ord(c) > 127 and c.isalpha() for c in s):
        return False
    # Find the longest segment between boundary chars
    longest = ""
    current = []
    for c in s:
        if c in _L5_BOUNDARY_CHARS:
            if len(current) > len(longest):
                longest = "".join(current)
            current = []
        else:
            current.append(c)
    if len(current) > len(longest):
        longest = "".join(current)
    if len(longest) < _L5_MIN_LEN:
        return False
    # camelCase: ≥2 occurrences of uppercase-followed-by-lowercase → real code
    camelcase_signals = sum(
        1 for i in range(len(longest) - 1)
        if longest[i].isupper() and longest[i + 1].islower()
    )
    if camelcase_signals >= 2:
        return False
    return True
