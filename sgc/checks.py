"""Deterministic screening checks (technical spec section 9.1).

These run in code, so they work even if the model misses something: hidden text, injection
phrases, and profanity. Each hit is a `FlagRecord` whose quote is text that exists in the
(redacted) submission.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .config import HIDDEN_EXCERPT_CHARS
from .flags import FlagRecord, HiddenText
from .llm.schemas import FlagType

# --- injection phrases --------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+|these\s+)?(?:previous|prior|above|earlier|preceding)\s+"
    r"(?:instructions?|rules|prompts?|directions?)",
    r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:rubric|instructions?|rules|above|previous)",
    r"ignore\s+(?:the|this|your)\s+rubric",
    r"system\s+prompt",
    r"you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|a\.i\.|assistant|grader|language\s+model|llm)\b",
    r"(?:give|assign|award|grant)\s+(?:me|this\s+(?:submission|essay|reflection|paper|assignment)|"
    r"the\s+student)\s+(?:\w+\s+){0,4}?(?:full|maximum|perfect|highest|top)\b",
    r"(?:full|maximum|perfect)\s+(?:marks|points|score|credit)\b",
    r"as\s+(?:the|an?)\s+(?:ai\s+)?grader",
    r"new\s+instructions?\s*:",
    r"</?\s*system\s*>",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?\n]")
_MAX_SENTENCE_CHARS = 300


def _sentence_around(text: str, start: int, end: int) -> Tuple[int, int]:
    """Widen a match to the sentence it sits in, so the whole instruction is quoted."""
    lo = start
    floor = max(0, start - _MAX_SENTENCE_CHARS)
    while lo > floor and not _SENTENCE_END.match(text[lo - 1]):
        lo -= 1
    hi = end
    ceil = min(len(text), end + _MAX_SENTENCE_CHARS)
    while hi < ceil and not _SENTENCE_END.match(text[hi]):
        hi += 1
    if hi < len(text) and text[hi] != "\n":
        hi += 1  # include the closing punctuation
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    return lo, hi


def find_injection_phrases(text: str) -> List[FlagRecord]:
    spans: List[Tuple[int, int]] = []
    for match in _INJECTION_RE.finditer(text):
        lo, hi = _sentence_around(text, match.start(), match.end())
        if spans and lo <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return [
        FlagRecord(
            type=FlagType.PROMPT_INJECTION.value,
            quote=text[lo:hi],
            explanation="Text that reads like an instruction to a grader or an AI system.",
            source="pattern",
            span=(lo, hi),
        )
        for lo, hi in spans
    ]


# --- profanity ----------------------------------------------------------------------------

_SUFFIXES = ("", "s", "es", "ed", "er", "ers", "ing", "in", "y")
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a", "!": "i"}
_WILDCARDS = set("*#%")
_OBFUSCATION_CHARS = _WILDCARDS | set(_LEET)
_EDGE_PUNCT = ".,;:?!\"'()[]{}<>\u201c\u201d\u2018\u2019"
_TOKEN_RE = re.compile(r"\S+")


@lru_cache(maxsize=1)
def _variants() -> Tuple[Set[str], List[str]]:
    path = Path(__file__).parent / "data" / "profanity.txt"
    words = [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    forms: Set[str] = set()
    for word in words:
        for suffix in _SUFFIXES:
            forms.add(word + suffix)
            if word.endswith("y") or word.endswith("e"):
                forms.add(word[:-1] + suffix)
    return forms, sorted(forms)


def _matches_obfuscated(candidate: str, form: str) -> bool:
    if len(candidate) != len(form):
        return False
    literal = 0
    for c, f in zip(candidate, form):
        if c == f:
            literal += 1
        elif c in _WILDCARDS or _LEET.get(c) == f:
            continue
        else:
            return False
    return literal >= 2


def _is_profane_token(token: str) -> bool:
    forms, ordered = _variants()
    stripped = token.strip(_EDGE_PUNCT).lower().rstrip("'")
    if len(stripped) < 3:
        return False
    candidates = {stripped}
    candidates.update(part for part in re.split(r"[-_/]", stripped) if part)
    collapsed = re.sub(r"[.\-_]", "", stripped)
    if len(collapsed) >= 4:
        candidates.add(collapsed)
    for candidate in candidates:
        if candidate in forms:
            return True
        if len(candidate) >= 4 and any(ch in _OBFUSCATION_CHARS for ch in candidate):
            if any(_matches_obfuscated(candidate, form) for form in ordered):
                return True
    return False


def _profanity_flag(quote: str, span: Tuple[int, int]) -> FlagRecord:
    return FlagRecord(
        type=FlagType.PROFANITY.value,
        quote=quote,
        explanation="Contains a word that is commonly considered profanity.",
        source="wordlist",
        span=span,
    )


def find_profanity(text: str) -> List[FlagRecord]:
    flags: List[FlagRecord] = []
    tokens = list(_TOKEN_RE.finditer(text))
    consumed: Set[int] = set()

    # Letters separated by spaces ("f u c k"): runs of single-character tokens.
    i = 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and len(tokens[j].group().strip(_EDGE_PUNCT)) == 1:
            j += 1
        if j - i >= 4:
            joined = "".join(t.group().strip(_EDGE_PUNCT) for t in tokens[i:j])
            if _is_profane_token(joined):
                start, end = tokens[i].start(), tokens[j - 1].end()
                flags.append(_profanity_flag(text[start:end], (start, end)))
                consumed.update(range(i, j))
        i = max(j, i + 1)

    for index, tok in enumerate(tokens):
        if index in consumed:
            continue
        if _is_profane_token(tok.group()):
            raw = tok.group()
            lead = len(raw) - len(raw.lstrip(_EDGE_PUNCT))
            trail = len(raw) - len(raw.rstrip(_EDGE_PUNCT))
            start, end = tok.start() + lead, tok.end() - trail
            flags.append(_profanity_flag(text[start:end], (start, end)))
    return flags


# --- hidden text --------------------------------------------------------------------------


def hidden_text_flags(hidden: Sequence[HiddenText]) -> List[FlagRecord]:
    flags = []
    for item in hidden:
        if not item.deliberate:
            continue
        excerpt = str(item.text).strip()
        if len(excerpt) > HIDDEN_EXCERPT_CHARS:
            excerpt = excerpt[:HIDDEN_EXCERPT_CHARS].rstrip() + "..."
        flags.append(
            FlagRecord(
                type=FlagType.PROMPT_INJECTION.value,
                quote=excerpt,
                explanation=(
                    f"Text in the file that is hidden from a reader ({item.location}). "
                    "It was not graded; it may be an attempt to instruct a grader or AI system."
                ),
                source="hidden_text",
                in_hidden=True,
            )
        )
    return flags


def run_checks(visible: str, hidden: Sequence[HiddenText]) -> List[FlagRecord]:
    return hidden_text_flags(hidden) + find_injection_phrases(visible) + find_profanity(visible)
