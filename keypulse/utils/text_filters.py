"""Text filtering utilities for IME composition and noise detection."""

from __future__ import annotations


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
