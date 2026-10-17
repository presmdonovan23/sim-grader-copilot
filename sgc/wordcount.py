"""Word counting (technical spec section 7).

Definition: split on whitespace; a token counts as a word if it contains at least one letter
or digit. Hyphenated words count as one word. Changing the rule only requires editing
`count_words`.
"""

from __future__ import annotations

from typing import Optional

from .config import WORD_COUNT_MAX, WORD_COUNT_MIN


def count_words(text: str) -> int:
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))


def word_count_flag(count: int) -> Optional[str]:
    """Return "under_min", "over_max", or None. Exactly the boundary values are in range."""
    if count < WORD_COUNT_MIN:
        return "under_min"
    if count > WORD_COUNT_MAX:
        return "over_max"
    return None
