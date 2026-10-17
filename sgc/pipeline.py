"""Per-submission orchestration (technical spec section 2).

`prepare_all` covers everything up to and including redaction (extraction, the quality gate, word
count, de-identification). `grade_all` then runs screening and grading on the prepared
submissions. Any failure makes that one submission "Not graded" with a reason; it never produces a
guessed grade.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import List, Optional

from .config import HIDDEN_EXCERPT_CHARS
from .discovery import Candidate
from .errors import NotGraded
from .extract import extract
from .extract.quality import check_quality
from .flags import FlagRecord, HiddenText
from .grade import GradeOutcome, grade_text
from .llm.base import LLMClient
from .llm.calllog import CallLog
from .llm.dryrun import DryRunComplete
from .llm.errors import ModelError, ModelUnavailable, SpendLimitReached, ValidationFailure
from .llm.spend import SpendTracker
from .redact import RedactedText, Redactor
from .rubric import Rubric
from .runlog import RunFolder, get_logger, submission_id, write_json
from .screen import model_screen, screen_submission
from .wordcount import word_count_flag

STATUS_READY = "ready"
STATUS_GRADED = "graded"
STATUS_NOT_GRADED = "not_graded"


@dataclass
class Prepared:
    sid: str
    filename: str
    status: str = STATUS_READY
    reason: Optional[str] = None
    word_count: Optional[int] = None
    word_count_flag: Optional[str] = None
    page_count: Optional[int] = None
    image_count: int = 0
    extractor: str = ""
    crosscheck_extractor: str = ""
    warnings: List[str] = field(default_factory=list)
    replacements: int = 0
    hidden: List[HiddenText] = field(default_factory=list)
    redacted: Optional[RedactedText] = None  # only set when the submission passed redaction
    flags: List[FlagRecord] = field(default_factory=list)
    grade: Optional[GradeOutcome] = None
    cost_usd: Optional[float] = None

    def to_result_json(self) -> dict:
        """Everything that is safe to write to disk. No filename and no un-redacted text."""
        return {
            "submission_id": self.sid,
            "status": self.status,
            "reason": self.reason,
            "word_count": self.word_count,
            "word_count_flag": self.word_count_flag,
            "page_count": self.page_count,
            "image_count": self.image_count,
            "extractor": self.extractor,
            "crosscheck_extractor": self.crosscheck_extractor,
            "warnings": self.warnings,
            "name_replacements": self.replacements,
            "hidden_text": [
                {"location": h.location, "text": str(h.text)[:HIDDEN_EXCERPT_CHARS]} for h in self.hidden
            ],
            "flags": [f.to_json() for f in self.flags],
            "grading": self.grade.to_json() if self.grade else None,
            "estimated_cost_usd": None if self.cost_usd is None else round(self.cost_usd, 4),
        }


def prepare_submission(candidate: Candidate, sid: str, redactor: Redactor) -> Prepared:
    item = Prepared(sid=sid, filename=candidate.name)
    try:
        if candidate.kind is None:
            raise NotGraded(candidate.unsupported_reason or "unsupported file type")
        doc = extract(candidate.path, candidate.kind)
        item.page_count = doc.page_count
        item.image_count = doc.image_count
        item.extractor = doc.extractor
        item.crosscheck_extractor = doc.crosscheck_extractor
        item.warnings = list(doc.warnings)

        words = check_quality(doc)
        item.word_count = words                       # counted on visible text, before redaction
        item.word_count_flag = word_count_flag(words)

        result = redactor.redact(doc.full_text)
        item.replacements = result.replacements
        for span in (s for s in doc.spans if not s.visible):
            item.hidden.append(HiddenText(span.location, redactor.redact(span.text).text))
        if doc.image_count:
            item.warnings.append(f"content not reviewed: {doc.image_count} image(s)")
        item.redacted = result.text
    except NotGraded as exc:
        _mark_not_graded(item, str(exc))
    except Exception:
        get_logger().error("Unexpected error processing %s:\n%s", sid, traceback.format_exc())
        _mark_not_graded(item, "internal error (see run.log)")
    return item


def _mark_not_graded(item: Prepared, reason: str) -> None:
    item.status = STATUS_NOT_GRADED
    item.reason = reason
    item.word_count = None
    item.word_count_flag = None
    item.replacements = 0
    item.hidden = []
    item.redacted = None
    item.flags = []
    item.grade = None
    get_logger().info("%s not graded: %s", item.sid, reason)


def prepare_all(candidates: List[Candidate], redactor: Redactor, run: RunFolder) -> List[Prepared]:
    results: List[Prepared] = []
    for index, candidate in enumerate(candidates, start=1):
        sid = submission_id(index)
        get_logger().info("Processing %s", sid)
        item = prepare_submission(candidate, sid, redactor)
        folder = run.submission_dir(sid)
        write_json(folder / "result.json", item.to_result_json())
        if item.redacted is not None:
            (folder / "redacted.txt").write_text(str(item.redacted), encoding="utf-8")
        results.append(item)
    return results


def _fail(item: Prepared, reason: str) -> None:
    """Model-stage failure: keep the flags found so far, but no grade."""
    item.status = STATUS_NOT_GRADED
    item.reason = reason
    item.grade = None
    get_logger().info("%s not graded: %s", item.sid, reason)


def grade_submission(
    item: Prepared, rubric: Rubric, llm: LLMClient, run: RunFolder, samples: int, spend: SpendTracker
) -> None:
    folder = run.submission_dir(item.sid)
    llm.use_call_log(CallLog(folder))
    assert item.redacted is not None
    try:
        item.flags, grading_text = screen_submission(llm, item.sid, item.redacted, item.hidden)
        item.grade = grade_text(llm, rubric, grading_text, item.sid, samples)
        item.status = STATUS_GRADED
        item.reason = None
    except SpendLimitReached:
        _fail(item, "spend limit reached (--max-spend)")
    except ModelUnavailable:
        _fail(item, "the model could not be reached (see run.log and llm_calls.md)")
    except ValidationFailure as exc:
        _fail(item, f"the model output could not be validated: {exc}")
    except ModelError as exc:
        _fail(item, f"model error: {exc}")
    except Exception:
        get_logger().error("Unexpected error grading %s:\n%s", item.sid, traceback.format_exc())
        _fail(item, "internal error (see run.log)")
    item.cost_usd = spend.by_submission.get(item.sid, 0.0) if spend.prices else None
    write_json(folder / "result.json", item.to_result_json())


def grade_all(
    items: List[Prepared], rubric: Rubric, llm: LLMClient, run: RunFolder, samples: int, spend: SpendTracker
) -> None:
    for item in items:
        if item.status != STATUS_READY:
            continue
        if spend.exhausted:
            _fail(item, "spend limit reached (--max-spend)")
            write_json(run.submission_dir(item.sid) / "result.json", item.to_result_json())
            continue
        get_logger().info("Grading %s", item.sid)
        grade_submission(item, rubric, llm, run, samples, spend)


def log_dry_run_request(item: Prepared, llm: LLMClient, run: RunFolder) -> None:
    """Write the first screening request, exactly as it would be sent, without sending it."""
    llm.use_call_log(CallLog(run.submission_dir(item.sid)))
    assert item.redacted is not None
    try:
        model_screen(llm, item.sid, item.redacted, item.hidden)
    except DryRunComplete:
        pass
