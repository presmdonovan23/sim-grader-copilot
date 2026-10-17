"""Per-submission orchestration (technical spec section 2).

This stage covers everything up to and including redaction: extraction, the quality gate, word
count, and de-identification. Model stages are added on top of `Prepared` submissions.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import List, Optional

from .discovery import Candidate
from .errors import NotGraded
from .extract import extract
from .extract.quality import check_quality
from .redact import RedactedText, Redactor
from .runlog import RunFolder, get_logger, submission_id, write_json
from .wordcount import word_count_flag

HIDDEN_EXCERPT_CHARS = 300

STATUS_READY = "ready"
STATUS_NOT_GRADED = "not_graded"


@dataclass
class HiddenText:
    location: str
    text: str  # redacted and truncated


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
    redacted: Optional[RedactedText] = None  # only set when status is "ready"

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
            "hidden_text": [{"location": h.location, "text": h.text} for h in self.hidden],
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
            hidden = redactor.redact(span.text).text
            item.hidden.append(HiddenText(span.location, str(hidden)[:HIDDEN_EXCERPT_CHARS]))
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
