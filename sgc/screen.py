"""Screening: deterministic checks plus a model pass for the judgment-based flags (spec section 9)."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from .checks import run_checks
from .config import MAX_VALIDATION_ATTEMPTS
from .flags import FlagRecord, HiddenText
from .llm.base import LLMClient
from .llm.errors import InvalidModelOutput, ValidationFailure
from .llm.prompts import INJECTION_PLACEHOLDER, SCREENING_SYSTEM, screening_prompt
from .llm.schemas import FlagType, ScreeningResult
from .redact import RedactedText, replace_spans
from .runlog import get_logger
from .verify import locate, normalize_quote


def _locate_flags(result: ScreeningResult, visible: str, hidden: Sequence[HiddenText]) -> List[FlagRecord]:
    flags: List[FlagRecord] = []
    for item in result.flags:
        quote = item.quote.strip()
        span = locate(visible, quote)
        in_hidden = False
        located = span is not None
        if span is None:
            in_hidden = any(locate(str(h.text), quote) is not None for h in hidden)
            located = in_hidden
        flags.append(
            FlagRecord(
                type=item.type.value,
                quote=quote,
                explanation=item.explanation.strip(),
                source="screening",
                located=located,
                in_hidden=in_hidden,
                span=span,
            )
        )
    return flags


def model_screen(
    llm: LLMClient, sid: str, visible: RedactedText, hidden: Sequence[HiddenText]
) -> List[FlagRecord]:
    """The screening call. Quotes that cannot be found get one more attempt; if they are still
    not found the flag is kept and marked as not located."""
    fallback: Optional[List[FlagRecord]] = None
    quote_retry_used = False
    last = "no attempts made"
    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        try:
            result = llm.complete_structured(
                stage="screening",
                submission_id=sid,
                attempt=attempt,
                system=SCREENING_SYSTEM,
                user=screening_prompt(visible, hidden, retry=attempt > 1),
                schema=ScreeningResult,
            )
        except InvalidModelOutput as exc:
            last = str(exc)
            get_logger().warning("%s screening attempt %d rejected: %s", sid, attempt, last)
            continue
        flags = _locate_flags(result, str(visible), hidden)
        if all(f.located for f in flags) or quote_retry_used:
            return flags
        quote_retry_used = True
        fallback = flags
        get_logger().warning("%s screening: a flag quote was not found in the text; retrying once", sid)
    if fallback is not None:
        return fallback
    raise ValidationFailure(f"screening output was still invalid after {MAX_VALIDATION_ATTEMPTS} attempts ({last})")


# --- merging ------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_quote(text.rstrip(".").rstrip())).lower()


def _same_finding(a: FlagRecord, b: FlagRecord) -> bool:
    if a.type != b.type:
        return False
    if a.span and b.span:
        return a.span[0] < b.span[1] and b.span[0] < a.span[1]
    qa, qb = _norm(a.quote), _norm(b.quote)
    return bool(qa and qb and (qa in qb or qb in qa))


def merge_flags(deterministic: Sequence[FlagRecord], model: Sequence[FlagRecord]) -> List[FlagRecord]:
    merged: List[FlagRecord] = list(deterministic)
    for flag in model:
        if not any(_same_finding(flag, existing) for existing in merged):
            merged.append(flag)
    return merged


def neutralize(visible: RedactedText, flags: Sequence[FlagRecord]) -> RedactedText:
    """Replace located prompt-injection text with a fixed marker before the grading call."""
    spans: List[Tuple[int, int]] = [
        f.span for f in flags
        if f.type == FlagType.PROMPT_INJECTION.value and f.span is not None and not f.in_hidden
    ]
    return replace_spans(visible, spans, INJECTION_PLACEHOLDER) if spans else visible


def screen_submission(
    llm: LLMClient, sid: str, visible: RedactedText, hidden: Sequence[HiddenText]
) -> Tuple[List[FlagRecord], RedactedText]:
    """Return (flags, text to grade). Raises a ModelError if screening cannot be completed."""
    deterministic = run_checks(str(visible), hidden)
    flags = merge_flags(deterministic, model_screen(llm, sid, visible, hidden))
    return flags, neutralize(visible, flags)
