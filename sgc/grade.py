"""Grading: validated samples, per-category median, and the weakest-area suggestion (spec section 10)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import MAX_EVIDENCE_QUOTES, MIN_VALID_SAMPLES
from .llm.base import LLMClient
from .llm.calls import call_validated
from .llm.errors import ModelError, ValidationFailure
from .llm.prompts import GRADING_SYSTEM, WEAKEST_AREA_SYSTEM, grading_prompt, weakest_area_prompt
from .llm.schemas import CategoryScore, GradingResult, WeakestAreaResult
from .redact import RedactedText
from .rubric import Category, Rubric
from .runlog import get_logger
from .verify import locate


@dataclass
class CategoryResult:
    category: str
    max_points: int
    points: int
    rationale: str
    quotes: List[str]
    borderline: bool = False
    alternative_points: Optional[int] = None
    borderline_reason: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "category": self.category,
            "max_points": self.max_points,
            "points": self.points,
            "rationale": self.rationale,
            "evidence_quotes": self.quotes,
            "borderline": self.borderline,
            "alternative_points": self.alternative_points,
            "borderline_reason": self.borderline_reason,
        }


@dataclass
class WeakestArea:
    category: str
    explanation: str
    suggestion: str
    quote: str

    def to_json(self) -> dict:
        return {
            "category": self.category,
            "explanation": self.explanation,
            "suggestion": self.suggestion,
            "quote": self.quote,
        }


@dataclass
class GradeOutcome:
    categories: List[CategoryResult]
    max_total: int
    samples_requested: int
    samples_valid: int
    weakest: Optional[WeakestArea] = None
    weakest_note: Optional[str] = None

    @property
    def total(self) -> int:
        return sum(c.points for c in self.categories)

    @property
    def borderline_categories(self) -> List[str]:
        return [c.category for c in self.categories if c.borderline]

    def to_json(self) -> dict:
        return {
            "total": self.total,
            "max_total": self.max_total,
            "samples_requested": self.samples_requested,
            "samples_valid": self.samples_valid,
            "categories": [c.to_json() for c in self.categories],
            "weakest_area": self.weakest.to_json() if self.weakest else None,
            "weakest_area_note": self.weakest_note,
        }


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


# --- one sample ---------------------------------------------------------------------------


def _adjacent(points: int, other: int, category: Category) -> bool:
    ordered = sorted(level.points for level in category.levels)
    return abs(ordered.index(points) - ordered.index(other)) == 1


def validate_sample(parsed: GradingResult, rubric: Rubric, text: str) -> List[CategoryResult]:
    """Check one grading output against the rubric and the text. Raises ValidationFailure."""
    by_name: Dict[str, CategoryScore] = {}
    for score in parsed.scores:
        key = _key(score.category)
        if key in by_name:
            raise ValidationFailure("a category appears more than once")
        by_name[key] = score
    expected = {_key(c.name) for c in rubric.categories}
    if set(by_name) != expected:
        raise ValidationFailure("the categories returned do not match the rubric")

    results: List[CategoryResult] = []
    for category in rubric.categories:
        score = by_name[_key(category.name)]
        valid_points = [level.points for level in category.levels]
        if score.points not in valid_points:
            raise ValidationFailure(f"'{category.name}': {score.points} is not a level of the rubric")
        if not score.rationale.strip():
            raise ValidationFailure(f"'{category.name}': empty rationale")
        quotes: List[str] = []
        for quote in score.evidence_quotes:
            if locate(text, quote) is not None and quote.strip() not in quotes:
                quotes.append(quote.strip())
        if score.points > 0 and not quotes:
            raise ValidationFailure(f"'{category.name}': no evidence quote could be found in the text")
        alternative = None
        reason = None
        if score.borderline:
            alternative = score.alternative_points
            if alternative is None or alternative not in valid_points or not _adjacent(score.points, alternative, category):
                raise ValidationFailure(f"'{category.name}': borderline without a valid adjacent alternative")
            reason = (score.borderline_reason or "").strip() or None
        results.append(
            CategoryResult(
                category=category.name,
                max_points=category.max_points,
                points=score.points,
                rationale=score.rationale.strip(),
                quotes=quotes[:MAX_EVIDENCE_QUOTES],
                borderline=bool(score.borderline),
                alternative_points=alternative,
                borderline_reason=reason,
            )
        )
    return results


# --- combining samples --------------------------------------------------------------------


def combine_samples(rubric: Rubric, samples: List[List[CategoryResult]]) -> List[CategoryResult]:
    """Per-category median (the lower median for an even count) with Borderline marking.

    A category is Borderline when samples disagree, or when a sample at the median score marked
    itself borderline. The rationale and quotes come from a sample at the median score.
    """
    count = len(samples)
    combined: List[CategoryResult] = []
    for index, category in enumerate(rubric.categories):
        entries = [sample[index] for sample in samples]
        scores = sorted(e.points for e in entries)
        median = scores[(count - 1) // 2]
        at_median = [e for e in entries if e.points == median]
        chosen = at_median[0]
        others = sorted({p for p in scores if p != median})
        borderline = False
        alternative: Optional[int] = None
        reason: Optional[str] = None
        if others:
            borderline = True
            alternative = min(others, key=lambda p: (abs(p - median), -p))
            reason = f"Sample scores differed ({', '.join(str(p) for p in scores)})."
            hinted = next((e for e in at_median if e.borderline and e.alternative_points == alternative), None)
            if hinted and hinted.borderline_reason:
                reason += " " + hinted.borderline_reason
        else:
            hinted = next((e for e in at_median if e.borderline), None)
            if hinted:
                borderline = True
                alternative = hinted.alternative_points
                reason = hinted.borderline_reason
        combined.append(
            CategoryResult(
                category=category.name,
                max_points=category.max_points,
                points=median,
                rationale=chosen.rationale,
                quotes=list(chosen.quotes),
                borderline=borderline,
                alternative_points=alternative,
                borderline_reason=reason,
            )
        )
    return combined


# --- full grading -------------------------------------------------------------------------


def grade_text(
    llm: LLMClient, rubric: Rubric, text: RedactedText, sid: str, samples: int
) -> GradeOutcome:
    """Run the grading samples. Raises ValidationFailure if too few valid samples remain, and
    lets ModelUnavailable, SpendLimitReached, and ModelAuthError through."""
    valid: List[List[CategoryResult]] = []
    for number in range(1, samples + 1):
        try:
            valid.append(
                call_validated(
                    llm,
                    stage="grading",
                    sid=sid,
                    system=GRADING_SYSTEM,
                    make_prompt=lambda retry: grading_prompt(rubric, text, retry=retry),
                    schema=GradingResult,
                    validate=lambda parsed: validate_sample(parsed, rubric, str(text)),
                )
            )
        except ValidationFailure as exc:
            get_logger().warning("%s grading sample %d discarded: %s", sid, number, exc)
    needed = min(MIN_VALID_SAMPLES, samples)
    if len(valid) < needed:
        raise ValidationFailure(f"only {len(valid)} of {samples} grading samples were valid")
    outcome = GradeOutcome(
        categories=combine_samples(rubric, valid),
        max_total=rubric.perfect_score,
        samples_requested=samples,
        samples_valid=len(valid),
    )
    if outcome.total == outcome.max_total:
        _add_weakest_area(llm, rubric, text, sid, outcome)
    return outcome


def _add_weakest_area(
    llm: LLMClient, rubric: Rubric, text: RedactedText, sid: str, outcome: GradeOutcome
) -> None:
    """Constructive feedback for a perfect score. Never affects scores; failure only adds a note."""

    def validate(parsed: WeakestAreaResult) -> WeakestArea:
        names = {_key(c.name): c.name for c in rubric.categories}
        category = names.get(_key(parsed.category))
        if category is None:
            raise ValidationFailure("the category is not in the rubric")
        if not parsed.explanation.strip() or not parsed.suggestion.strip():
            raise ValidationFailure("empty explanation or suggestion")
        if locate(str(text), parsed.quote) is None:
            raise ValidationFailure("the quote could not be found in the text")
        return WeakestArea(category, parsed.explanation.strip(), parsed.suggestion.strip(), parsed.quote.strip())

    try:
        outcome.weakest = call_validated(
            llm,
            stage="weakest_area",
            sid=sid,
            system=WEAKEST_AREA_SYSTEM,
            make_prompt=lambda retry: weakest_area_prompt(rubric, text, retry=retry),
            schema=WeakestAreaResult,
            validate=validate,
        )
    except ModelError as exc:
        get_logger().warning("%s weakest-area suggestion not produced: %s", sid, exc)
        outcome.weakest_note = "A suggestion for improvement could not be produced."
