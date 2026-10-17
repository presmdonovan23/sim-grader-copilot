"""Find student names in submissions without any language model.

This is a helper for building the names file that SGC uses for redaction. It reads each
submission (PDF or DOCX) locally and looks for names in the places where students usually put
them: the document properties, a "Name:" label, header or footer text, the first lines of the
document, and the file name. Nothing is sent anywhere.

Names that show up in many submissions (the simulated patients, the instructor, the course
title) are reported separately and are not treated as student names.

The result is a list of candidates for a person to review, not a finished names file.
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from lxml import etree

from .errors import NotGraded
from .extract import extract

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}
_SOURCE_PRIORITY = ["label", "metadata", "header/footer", "top of document", "file name", "near top", "end of document"]

_PARTICLES = {"de", "la", "van", "von", "der", "del", "di", "da", "bin", "al", "el", "le", "du", "dos", "den"}
_TOKEN = re.compile(
    r"^(?:[A-Z][a-z\u00e0-\u00ff'\u2019\-]+(?:[A-Z][a-z\u00e0-\u00ff]+)?"   # Becca, O'Brien, McCoy, Smith-Jones
    r"|[A-Z]\.)$"                                                            # initial
)
_CAPS_TOKEN = re.compile(r"^[A-Z][A-Z'\u2019\-]+$")

_STOP = set(
    """
    simulation simulations sim reflection reflections pediatric pediatrics nursing nurse nurses documentation
    part post pre simemr emr vital signs assessment physical clinical judgment decision making patient
    patients course class instructor professor assignment assignments lab labs evaluation scenario case study care
    health healthcare introduction conclusion summary title page date name student students university college
    school department program fall spring summer winter january february march april may june july august
    september october november december monday tuesday wednesday thursday friday saturday sunday unit week
    module nsg nur hesi sbar soap debriefing debrief communication takeaways feedback peer self awareness
    insight depth the and of in on for to with a an my our your this that final draft copy version
    submitted submission due grade grading rubric points total homework hw document microsoft word office
    user admin owner author unknown anonymous windows computer laptop mac pc untitled
    pediatric adult maternal newborn medical surgical mental psychiatric community critical
    """.split()
)

_LABEL = re.compile(
    r"^\s*(?:student\s+name|full\s+name|your\s+name|name|student|submitted\s+by|prepared\s+by|written\s+by|"
    r"author|by)\s*[:\-\u2013\u2014]\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_NEXT_FIELD = re.compile(r"\s+(?:date|course|class|section|instructor|professor|id|group|sim)\s*[:#]", re.IGNORECASE)
_SPLIT_FIELDS = re.compile(r"\s{2,}|\t|\||\u2022|\u00b7|\s[-\u2013\u2014]\s")
_SPLIT_PEOPLE = re.compile(r"\s+(?:and|&)\s+|\s*[,/;]\s*(?=[A-Z])")
_LAST_FIRST = re.compile(
    r"^(?P<last>[A-Z][A-Za-z\u00e0-\u00ff'\u2019\-]+),\s+(?P<first>[A-Z][A-Za-z\u00e0-\u00ff'\u2019\-]+(?:\s+[A-Z]\.?)?)$"
)
_MAX_CORE_XML_BYTES = 1_000_000


@dataclass
class Found:
    name: str
    source: str
    confidence: str


@dataclass
class Candidate:
    name: str
    key: Tuple[str, ...]
    files: List[str] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    confidence: str = LOW
    docs_with_text: int = 0


@dataclass
class ScanResult:
    candidates: List[Candidate]          # likely student names, best first
    shared: List[Candidate]              # appear in many submissions: patients, instructor, course
    per_file: Dict[str, List[Found]]
    unreadable: Dict[str, str]
    no_name: List[str]                   # files with no high or medium candidate
    shared_threshold: int = 0


# --- name shape ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def normalize_candidate(text: str, allow_lower: bool = False) -> Optional[str]:
    """Return a tidy "First Last" string if `text` looks like a person's name, else None.

    All-lowercase text is only accepted when `allow_lower` is set (document properties), because
    ordinary lowercase prose would otherwise look like names.
    """
    text = _clean(text).strip(" ,.;:()[]\"'")
    if not text or any(ch.isdigit() or ch in "@/\\_<>=+#%$" for ch in text) or text.endswith(":"):
        return None
    match = _LAST_FIRST.match(text)
    if match:
        text = f"{match.group('first')} {match.group('last')}"
    tokens = text.split()
    if not 2 <= len(tokens) <= 4:
        return None
    if all(_CAPS_TOKEN.match(t) or _TOKEN.match(t) for t in tokens) and any(_CAPS_TOKEN.match(t) and len(t) > 2 for t in tokens):
        tokens = [t.capitalize() if _CAPS_TOKEN.match(t) else t for t in tokens]
    elif allow_lower and text == text.lower():
        tokens = [t.capitalize() if t not in _PARTICLES else t for t in tokens]
    for index, token in enumerate(tokens):
        low = token.lower().strip(".")
        if low in _STOP:
            return None
        if token in _PARTICLES and 0 < index < len(tokens) - 1:
            continue
        if not _TOKEN.match(token):
            return None
    for end in (tokens[0], tokens[-1]):
        if len(end.strip(".")) < 2 or end.lower() in _PARTICLES:
            return None
    return " ".join(tokens)


def _names_in_segment(segment: str, allow_lower: bool = False) -> List[str]:
    found = []
    for part in _SPLIT_PEOPLE.split(segment):
        name = normalize_candidate(part, allow_lower)
        if name:
            found.append(name)
    if not found:
        name = normalize_candidate(segment, allow_lower)
        if name:
            found.append(name)
    return found


def _names_in_line(line: str) -> List[str]:
    names: List[str] = []
    for piece in _SPLIT_FIELDS.split(line):
        names.extend(_names_in_segment(piece))
    return names


# --- sources ------------------------------------------------------------------------------


def _metadata_names(path: Path, kind: str) -> List[str]:
    values: List[str] = []
    try:
        if kind == "pdf":
            from pypdf import PdfReader

            meta = PdfReader(str(path)).metadata
            if meta is not None and meta.author:
                values.append(str(meta.author))
        elif kind == "docx":
            with zipfile.ZipFile(path) as zf:
                info = zf.getinfo("docProps/core.xml")
                if info.file_size <= _MAX_CORE_XML_BYTES:
                    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                    root = etree.fromstring(zf.read(info), parser)
                    for el in root.iter():
                        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] in ("creator", "lastModifiedBy") and el.text:
                            values.append(el.text)
    except Exception:
        return []
    names = []
    for value in values:
        names.extend(_names_in_segment(value, allow_lower=True))
    return names


def _filename_names(path: Path) -> List[str]:
    parts = [p for p in re.split(r"[_\-\s.]+|(?<=[a-z])(?=[A-Z])", path.stem) if p]
    parts = [p for p in parts if p.isalpha() and p.lower() not in _STOP and len(p) >= 2]
    if 2 <= len(parts) <= 3:
        name = normalize_candidate(" ".join(p.capitalize() for p in parts))
        return [name] if name else []
    return []


def scan_document(path: Path, kind: str) -> Tuple[List[Found], str]:
    """Candidate names from one submission, and its visible text (for cross-document counts)."""
    found: List[Found] = []
    text = ""
    try:
        doc = extract(path, kind)
    except NotGraded as exc:
        for name in _filename_names(path):
            found.append(Found(name, "file name", LOW))
        raise _Unreadable(str(exc), found) from exc

    text = doc.full_text
    lines = [_clean(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    for name in _metadata_names(path, kind):
        found.append(Found(name, "metadata", MEDIUM))
    for span in doc.spans:
        if span.visible and span.location in ("header", "footer"):
            for line in span.text.splitlines():
                for name in _names_in_line(_clean(line)):
                    found.append(Found(name, "header/footer", MEDIUM))
    raw_lines = [line.replace("\u00a0", " ").strip() for line in text.splitlines()]
    for line in [line for line in raw_lines if line][:300]:
        match = _LABEL.match(line)
        if match:
            rest = _NEXT_FIELD.split(match.group("rest"))[0]
            for piece in _SPLIT_FIELDS.split(rest)[:1]:
                for name in _names_in_segment(_clean(piece)):
                    found.append(Found(name, "label", HIGH))
    for index, line in enumerate(lines[:10]):
        for name in _names_in_line(line):
            if index < 3:
                found.append(Found(name, "top of document", MEDIUM))
            else:
                found.append(Found(name, "near top", LOW))
    for line in lines[-3:]:
        for name in _names_in_line(line):
            found.append(Found(name, "end of document", LOW))
    for name in _filename_names(path):
        found.append(Found(name, "file name", LOW))
    return found, text


class _Unreadable(Exception):
    def __init__(self, reason: str, found: List[Found]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.found = found


# --- directory scan -----------------------------------------------------------------------


def _key(name: str) -> Tuple[str, ...]:
    return tuple(sorted(t.lower().strip(".") for t in name.split() if t.lower() not in _PARTICLES))


def _merge_for_file(found: List[Found]) -> List[Found]:
    """One entry per person in a file; a name seen in two kinds of place becomes high confidence."""
    groups: Dict[Tuple[str, ...], List[Found]] = {}
    for item in found:
        groups.setdefault(_key(item.name), []).append(item)
    merged: List[Found] = []
    for items in groups.values():
        sources = sorted({i.source for i in items}, key=_SOURCE_PRIORITY.index)
        best = max(items, key=lambda i: _RANK[i.confidence])
        confidence = best.confidence
        if len(sources) >= 2 and _RANK[confidence] >= _RANK[MEDIUM]:
            confidence = HIGH
        elif len(sources) >= 2 and "file name" in sources:
            confidence = MEDIUM
        display = next(i.name for s in _SOURCE_PRIORITY for i in items if i.source == s)
        merged.append(Found(display, ", ".join(sources), confidence))
    merged.sort(key=lambda f: -_RANK[f.confidence])
    return merged


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold())


def scan_directory(directory: Path) -> ScanResult:
    files = sorted(
        p for p in Path(directory).iterdir()
        if p.is_file() and not p.name.startswith((".", "~$")) and p.suffix.lower() in (".pdf", ".docx")
    )
    per_file: Dict[str, List[Found]] = {}
    texts: Dict[str, str] = {}
    unreadable: Dict[str, str] = {}
    for path in files:
        kind = path.suffix.lower().lstrip(".")
        try:
            found, text = scan_document(path, kind)
            texts[path.name] = _normalized_text(text)
        except _Unreadable as exc:
            found = exc.found
            unreadable[path.name] = exc.reason
        per_file[path.name] = _merge_for_file(found)

    threshold = max(3, math.ceil(0.15 * len(files)))
    candidates: Dict[Tuple[str, ...], Candidate] = {}
    for filename, items in per_file.items():
        for item in items:
            key = _key(item.name)
            cand = candidates.setdefault(key, Candidate(name=item.name, key=key))
            cand.files.append(filename)
            cand.sources.update(s.strip() for s in item.source.split(","))
            if _RANK[item.confidence] > _RANK[cand.confidence]:
                cand.confidence = item.confidence
    for cand in candidates.values():
        needle = _normalized_text(cand.name)
        cand.docs_with_text = sum(1 for text in texts.values() if needle in text)

    shared = [c for c in candidates.values() if len(c.files) >= threshold or c.docs_with_text >= threshold]
    shared_keys = {c.key for c in shared}
    likely = sorted(
        (c for c in candidates.values() if c.key not in shared_keys),
        key=lambda c: (-_RANK[c.confidence], c.name.lower()),
    )
    for filename in per_file:
        per_file[filename] = [f for f in per_file[filename] if _key(f.name) not in shared_keys]
    no_name = [f for f, items in per_file.items() if not any(_RANK[i.confidence] >= _RANK[MEDIUM] for i in items)]
    return ScanResult(likely, sorted(shared, key=lambda c: -c.docs_with_text), per_file, unreadable, no_name, threshold)


# --- output -------------------------------------------------------------------------------


def format_candidates_file(result: ScanResult) -> str:
    lines = [
        "# Candidate student names found by scripts/extract_names.py (no language model was used).",
        "# Review before use: fix spelling, add anyone who is missing, then copy the names you want",
        "# into your names file. Lines starting with '#?' are low-confidence guesses; remove the",
        "# '#?' to include one. This file contains real names: keep it out of git.",
        "",
    ]
    for cand in result.candidates:
        where = ", ".join(sorted(cand.files)[:5]) + (" ..." if len(cand.files) > 5 else "")
        lines.append(f"# {where} ({', '.join(sorted(cand.sources))}; {cand.confidence})")
        lines.append(cand.name if cand.confidence in (HIGH, MEDIUM) else f"#? {cand.name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
