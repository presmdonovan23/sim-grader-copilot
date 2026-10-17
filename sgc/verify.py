"""Locate model-supplied quotes in the text (technical spec section 10.5).

Matching is exact apart from whitespace runs, curly versus straight quotes, and dash variants.
It is case-sensitive and never skips words, so a "quote" the model shortened or joined with an
ellipsis is not located unless the student's text itself contains those exact characters.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

_QUOTE_MAP = {
    "\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'", "`": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2212": "-",
}
_WS = re.compile(r"\s+")


def _normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """Normalized text and, for each normalized char, its index in the original."""
    out: List[str] = []
    index: List[int] = []
    prev_space = True  # also trims leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                index.append(i)
            prev_space = True
            continue
        out.append(_QUOTE_MAP.get(ch, ch))
        index.append(i)
        prev_space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def normalize_quote(quote: str) -> str:
    normalized, _ = _normalize_with_map(quote)
    # The model sometimes wraps a quote in quotation marks of its own.
    if len(normalized) >= 2 and normalized[0] == '"' and normalized[-1] == '"' and '"' not in normalized[1:-1]:
        normalized = normalized[1:-1].strip()
    return normalized


def locate(text: str, quote: str) -> Optional[Tuple[int, int]]:
    """Return (start, end) of the quote within `text`, or None if it is not there verbatim."""
    needle = normalize_quote(quote)
    if len(needle) < 3:
        return None
    haystack, index = _normalize_with_map(text)
    pos = haystack.find(needle)
    if pos < 0:
        return None
    start = index[pos]
    end = index[pos + len(needle) - 1] + 1
    return start, end
