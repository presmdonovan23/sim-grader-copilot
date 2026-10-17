"""Quality gate (technical spec section 6.5).

A submission that fails any check is Not graded, with a specific reason. All thresholds are
in config.py.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Set

from .. import config
from ..errors import NotGraded
from ..wordcount import count_words
from .types import ExtractedDocument

_TOKEN_RE = re.compile(r"[^\W_]+")

FUNCTION_WORDS = frozenset(
    """the and of to a in is that it for with as was on are be this by at or from an but not
    have had has i we my our you they he she his her their were which what when how if so can
    would could should will do did me us them been there than then also more about into out
    up one all""".split()
)


def tokens(text: str) -> list:
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).casefold())


def _token_set(text: str) -> Set[str]:
    return set(tokens(text))


def _check_extractors_agree(doc: ExtractedDocument) -> None:
    if not doc.crosscheck_extractor:
        return
    primary, other = doc.crosscheck_scope_text, doc.crosscheck_text
    a, b = count_words(primary), count_words(other)
    biggest = max(a, b)
    if biggest and abs(a - b) / biggest > config.EXTRACTOR_MAX_WORD_DIFF:
        raise NotGraded(
            f"text extractors disagree on the word count ({a} vs {b}), so the text may not "
            "have been read reliably"
        )
    set_a, set_b = _token_set(primary), _token_set(other)
    union = set_a | set_b
    if union and len(set_a & set_b) / len(union) < config.EXTRACTOR_MIN_TOKEN_OVERLAP:
        raise NotGraded("text extractors disagree on the content, so the text may not have been read reliably")


def _check_not_garbled(text: str, words: int) -> None:
    if words < config.GARBLE_MIN_WORDS_TO_CHECK:
        return
    total = len(text)
    normal = bad = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if ch == "\ufffd" or cat == "Co":
            bad += 1
        elif cat[0] in "LNMZP" or ch.isspace() or cat in ("Sc", "Sm"):
            normal += 1
    if normal / total < config.GARBLE_MIN_NORMAL_CHAR_FRACTION:
        raise NotGraded("text appears garbled (too many unusual characters)")
    if bad / total > config.GARBLE_MAX_BAD_CHAR_FRACTION:
        raise NotGraded("text appears garbled (replacement or private-use characters)")
    toks = tokens(text)
    if toks and sum(1 for t in toks if t in FUNCTION_WORDS) / len(toks) < config.GARBLE_MIN_FUNCTION_WORD_FRACTION:
        raise NotGraded("text does not look like readable English prose")


def check_quality(doc: ExtractedDocument) -> int:
    """Raise NotGraded if the document cannot be reliably graded. Returns the word count."""
    text = doc.full_text
    words = count_words(text)
    if doc.image_only_pages:
        raise NotGraded("contains pages with no extractable text (possible scan)")
    if words < 1:
        raise NotGraded("no text could be extracted from the file")

    if doc.page_count and doc.text_free_pages / doc.page_count > config.MAX_TEXT_FREE_PAGE_FRACTION:
        raise NotGraded("contains pages with no extractable text (possible scan)")

    _check_extractors_agree(doc)
    _check_not_garbled(text, words)

    if words > config.MAX_WORDS_FOR_GRADING:
        raise NotGraded(f"too long to grade reliably ({words} words; limit {config.MAX_WORDS_FOR_GRADING})")
    return words
