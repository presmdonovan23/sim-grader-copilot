"""Types shared by the extractors (technical spec section 6.2)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TextSpan(BaseModel):
    text: str
    visible: bool          # False for text hidden from the reader (white, tiny, vanish, deleted, ...)
    location: str          # "page 3", "header", "table 2", ...


class ExtractedDocument(BaseModel):
    spans: List[TextSpan]
    page_count: Optional[int] = None
    image_count: int = 0
    extractor: str
    warnings: List[str] = Field(default_factory=list)

    # PDF only: pages used by the quality gate to detect scans.
    text_free_pages: int = 0
    image_only_pages: int = 0

    # Text from the independent cross-check extractor. It covers the same content as
    # `crosscheck_scope_text` below and is used only for the extractor-agreement check.
    crosscheck_extractor: str = ""
    crosscheck_text: str = ""
    crosscheck_scope_text: str = ""

    @property
    def full_text(self) -> str:
        """Visible text only, in reading order. This is what is counted, redacted and graded."""
        return "\n".join(s.text for s in self.spans if s.visible)

    @property
    def hidden_text(self) -> str:
        return "\n".join(s.text for s in self.spans if not s.visible)
