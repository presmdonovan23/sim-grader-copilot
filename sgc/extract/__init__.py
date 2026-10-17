"""Text extraction for supported file types."""

from __future__ import annotations

from pathlib import Path

from ..config import MAX_FILE_BYTES
from ..errors import NotGraded
from .types import ExtractedDocument


def extract(path: Path, kind: str) -> ExtractedDocument:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NotGraded(f"file could not be read ({exc.strerror or exc})") from exc
    if size == 0:
        raise NotGraded("file is empty")
    if size > MAX_FILE_BYTES:
        raise NotGraded("file is too large")
    if kind == "pdf":
        from .pdf import extract_pdf
        return extract_pdf(path)
    if kind == "docx":
        from .docx import extract_docx
        return extract_docx(path)
    raise NotGraded(f"unsupported file type ({kind})")
