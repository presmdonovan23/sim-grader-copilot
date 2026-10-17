"""Find submissions in a directory (technical spec section 6.1)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import REPORT_PREFIX
from .errors import FatalError

SUPPORTED_SUFFIXES = {".pdf": "pdf", ".docx": "docx"}


@dataclass
class Candidate:
    path: Path
    kind: Optional[str]  # "pdf", "docx", or None when the file type is unsupported

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def unsupported_reason(self) -> Optional[str]:
        if self.kind is not None:
            return None
        suffix = self.path.suffix.lower()
        return f"unsupported file type ({suffix or 'no extension'})"


def natural_key(name: str):
    """Sort key so that 2.pdf comes before 10.pdf."""
    parts = re.split(r"(\d+)", name.lower())
    return [(0, int(p)) if p.isdigit() else (1, p) for p in parts]


def _is_ignored(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if name.startswith("."):          # hidden files, .DS_Store
        return True
    if name.startswith("~$"):         # Office lock files
        return True
    if lower.startswith(REPORT_PREFIX) and lower.endswith(".pdf"):  # SGC's own reports
        return True
    return False


def check_directory(directory: Path) -> None:
    if not directory.exists():
        raise FatalError(f"Submissions directory does not exist: {directory}")
    if not directory.is_dir():
        raise FatalError(f"Not a directory: {directory}")
    if not os.access(directory, os.R_OK | os.X_OK):
        raise FatalError(f"Submissions directory is not readable: {directory}")
    if not os.access(directory, os.W_OK):
        raise FatalError(
            f"Submissions directory is not writable: {directory} "
            "(the report and run files are written there)"
        )


def discover(directory: Path) -> List[Candidate]:
    """Return candidate submissions sorted by filename. Subdirectories are not searched."""
    check_directory(directory)
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise FatalError(f"Cannot list {directory}: {exc.strerror or exc}") from exc

    candidates = []
    for path in entries:
        if _is_ignored(path) or not path.is_file():
            continue
        candidates.append(Candidate(path=path, kind=SUPPORTED_SUFFIXES.get(path.suffix.lower())))
    if not candidates:
        raise FatalError(f"No submissions found in {directory}")
    candidates.sort(key=lambda c: natural_key(c.name))
    return candidates
