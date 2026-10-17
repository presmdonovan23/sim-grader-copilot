"""PDF extraction (technical spec section 6.3).

Primary extractor: pdfplumber (gives per-character size and color, so hidden text can be
detected). Cross-check extractor: pypdf. Images are counted and never sent to a model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List

import pdfplumber
import pypdf

from ..config import HIDDEN_FONT_SIZE_PT, NEAR_WHITE_THRESHOLD
from ..errors import NotGraded
from .types import ExtractedDocument, TextSpan

# Library chatter about slightly malformed PDFs is not useful to the instructor.
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def _numbers(color: Any) -> List[float]:
    if color is None:
        return []
    if isinstance(color, (int, float)):
        return [float(color)]
    try:
        return [float(c) for c in color]
    except (TypeError, ValueError):
        return []  # pattern or named colors


def is_white(color: Any) -> bool:
    """True for white or near-white in gray, RGB, or CMYK."""
    nums = _numbers(color)
    if len(nums) == 1 or len(nums) == 3:
        return all(n >= NEAR_WHITE_THRESHOLD for n in nums)
    if len(nums) == 4:
        return all(n <= 1 - NEAR_WHITE_THRESHOLD for n in nums)
    return False


def _dark_rects(page: Any) -> List[Any]:
    """Filled rectangles with a non-white fill. White text on one of these is legitimate."""
    rects = []
    for rect in page.rects:
        if rect.get("fill") and _numbers(rect.get("non_stroking_color")) and not is_white(
            rect.get("non_stroking_color")
        ):
            rects.append(rect)
    return rects


def _over_dark_rect(char: Any, rects: List[Any]) -> bool:
    cx = (char["x0"] + char["x1"]) / 2
    cy = (char["top"] + char["bottom"]) / 2
    return any(r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"] for r in rects)


def _make_hidden_test(page: Any):
    x0, top, x1, bottom = page.bbox
    rects = _dark_rects(page)

    def hidden(obj: Any) -> bool:
        if obj.get("object_type") != "char":
            return False
        size = obj.get("size")
        if size is not None and 0 < size < HIDDEN_FONT_SIZE_PT:
            return True
        if obj["x1"] <= x0 or obj["x0"] >= x1 or obj["bottom"] <= top or obj["top"] >= bottom:
            return True
        if is_white(obj.get("non_stroking_color")) and not _over_dark_rect(obj, rects):
            return True
        return False

    return hidden


def extract_pdf(path: Path) -> ExtractedDocument:
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            raise NotGraded("file is encrypted or password-protected")
        crosscheck_pages = [(p.extract_text() or "") for p in reader.pages]
    except NotGraded:
        raise
    except Exception as exc:  # corrupt or unusual file
        raise NotGraded(f"PDF could not be opened ({type(exc).__name__})") from exc

    spans: List[TextSpan] = []
    warnings: List[str] = []
    image_count = 0
    text_free_pages = 0
    image_only_pages = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                raise NotGraded("PDF has no pages")
            for number, page in enumerate(pdf.pages, start=1):
                hidden_test = _make_hidden_test(page)
                has_hidden = any(hidden_test(c) for c in page.chars)
                if has_hidden:
                    visible_page = page.filter(lambda o: not hidden_test(o))
                    hidden_page = page.filter(lambda o: o.get("object_type") == "char" and hidden_test(o))
                    visible_text = visible_page.extract_text() or ""
                    hidden_text = hidden_page.extract_text() or ""
                else:
                    visible_text = page.extract_text() or ""
                    hidden_text = ""
                location = f"page {number}"
                if visible_text.strip():
                    spans.append(TextSpan(text=visible_text, visible=True, location=location))
                if hidden_text.strip():
                    spans.append(TextSpan(text=hidden_text, visible=False, location=location))
                page_images = len(page.images)
                image_count += page_images
                if not visible_text.strip():
                    text_free_pages += 1
                    if page_images:
                        image_only_pages += 1
    except NotGraded:
        raise
    except Exception as exc:
        raise NotGraded(f"PDF could not be read ({type(exc).__name__})") from exc

    return ExtractedDocument(
        spans=spans,
        page_count=page_count,
        image_count=image_count,
        extractor=f"pdfplumber {pdfplumber.__version__}",
        warnings=warnings,
        text_free_pages=text_free_pages,
        image_only_pages=image_only_pages,
        crosscheck_extractor=f"pypdf {pypdf.__version__}",
        crosscheck_text="\n".join(crosscheck_pages),
        # pypdf also returns hidden text, so compare against everything the primary extractor saw.
        crosscheck_scope_text="\n".join(s.text for s in spans),
    )
