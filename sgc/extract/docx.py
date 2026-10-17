"""DOCX extraction (technical spec section 6.4).

The document XML is walked directly with lxml so that text is captured from body paragraphs,
tables (including nested), headers and footers, footnotes and endnotes, and text boxes.
python-docx is used as the independent cross-check.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import docx
from lxml import etree

from ..config import HIDDEN_FONT_SIZE_PT, MAX_DOCX_UNCOMPRESSED_BYTES, NEAR_WHITE_THRESHOLD
from ..errors import NotGraded
from .types import ExtractedDocument, TextSpan

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _w(tag: str) -> str:
    return "{%s}%s" % (W_NS, tag)


def _mc(tag: str) -> str:
    return "{%s}%s" % (MC_NS, tag)


_W_VAL = _w("val")

# Elements that wrap runs and are transparent for text purposes.
_TRANSPARENT = {
    _w("ins"), _w("moveTo"), _w("hyperlink"), _w("smartTag"), _w("sdt"), _w("sdtContent"),
    _w("fldSimple"), _w("customXml"), _w("dir"), _w("bdo"),
}
_DELETED_WRAPPERS = {_w("del"), _w("moveFrom")}
_WHITE_THEME_COLORS = {"background1", "bg1", "light1"}
_SKIPPED_NOTE_TYPES = {"separator", "continuationSeparator", "continuationNotice"}

_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
    remove_comments=True, remove_pis=True,
)


def _on(el: Optional[etree._Element]) -> bool:
    """A toggle property like <w:vanish/> is on unless its value says otherwise."""
    if el is None:
        return False
    return el.get(_W_VAL, "true").lower() not in ("0", "false", "off", "none")


def _hex_is_near_white(value: Optional[str]) -> bool:
    if not value or len(value) != 6:
        return False
    try:
        channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return False
    return all(c >= NEAR_WHITE_THRESHOLD for c in channels)


def _shading_is_contrasting(shd: Optional[etree._Element]) -> bool:
    if shd is None:
        return False
    fill = shd.get(_w("fill"))
    if fill is None or fill.lower() == "auto":
        return False
    return not _hex_is_near_white(fill)


def _run_hidden_reason(rpr: Optional[etree._Element], shaded: bool) -> Optional[str]:
    if rpr is None:
        return None
    if _on(rpr.find(_w("vanish"))):
        return "hidden text"
    sz = rpr.find(_w("sz"))
    if sz is not None:
        try:
            if int(sz.get(_W_VAL, "")) / 2 <= HIDDEN_FONT_SIZE_PT:
                return "tiny text"
        except ValueError:
            pass
    color = rpr.find(_w("color"))
    if color is not None:
        value = color.get(_W_VAL)
        white = _hex_is_near_white(value) or color.get(_w("themeColor"), "") in _WHITE_THEME_COLORS
        if white:
            highlight = rpr.find(_w("highlight"))
            highlighted = highlight is not None and highlight.get(_W_VAL, "none") not in ("none", "white")
            if not (shaded or highlighted or _shading_is_contrasting(rpr.find(_w("shd")))):
                return "white text"
    return None


class _Walker:
    def __init__(self) -> None:
        self.spans: List[TextSpan] = []
        self._tables = 0

    # --- blocks -------------------------------------------------------------------------
    def blocks(self, container: etree._Element, location: str, shaded: bool = False) -> None:
        for child in container:
            tag = child.tag
            if tag == _w("p"):
                self.paragraph(child, location, shaded)
            elif tag == _w("tbl"):
                self._tables += 1
                self.table(child, f"table {self._tables}", shaded)
            elif tag == _w("sdt"):
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self.blocks(content, location, shaded)
            elif tag == _mc("AlternateContent"):
                for choice in child.findall(_mc("Choice")):
                    self.blocks(choice, location, shaded)
            elif tag in (_w("ins"), _w("moveTo"), _w("customXml")):
                self.blocks(child, location, shaded)

    def table(self, tbl: etree._Element, location: str, shaded: bool) -> None:
        for row in tbl.findall(_w("tr")):
            for cell in row.findall(_w("tc")):
                tcpr = cell.find(_w("tcPr"))
                cell_shaded = shaded or _shading_is_contrasting(
                    tcpr.find(_w("shd")) if tcpr is not None else None
                )
                self.blocks(cell, location, cell_shaded)

    # --- paragraphs ---------------------------------------------------------------------
    def paragraph(self, p: etree._Element, location: str, shaded: bool) -> None:
        ppr = p.find(_w("pPr"))
        p_shaded = shaded or _shading_is_contrasting(ppr.find(_w("shd")) if ppr is not None else None)
        visible: List[str] = []
        hidden: List[Tuple[str, str]] = []  # (reason, text)
        boxes: List[etree._Element] = []

        for run, deleted in self._runs(p, False):
            text, run_boxes = self._run_content(run)
            boxes.extend(run_boxes)
            if not text:
                continue
            if deleted:
                hidden.append(("deleted tracked change", text))
                continue
            reason = _run_hidden_reason(run.find(_w("rPr")), p_shaded)
            if reason:
                hidden.append((reason, text))
            else:
                visible.append(text)

        joined = "".join(visible)
        if joined.strip():
            self.spans.append(TextSpan(text=joined, visible=True, location=location))
        by_reason: dict = {}
        for reason, text in hidden:
            by_reason.setdefault(reason, []).append(text)
        for reason, texts in by_reason.items():
            joined_hidden = "".join(texts)
            if joined_hidden.strip():
                self.spans.append(
                    TextSpan(text=joined_hidden, visible=False, location=f"{location} ({reason})")
                )
        for box in boxes:
            self.blocks(box, "text box", p_shaded)

    def _runs(self, node: etree._Element, deleted: bool) -> Iterator[Tuple[etree._Element, bool]]:
        for child in node:
            tag = child.tag
            if tag == _w("r"):
                yield child, deleted
            elif tag in _DELETED_WRAPPERS:
                yield from self._runs(child, True)
            elif tag in _TRANSPARENT:
                yield from self._runs(child, deleted)
            elif tag == _mc("AlternateContent"):
                for choice in child.findall(_mc("Choice")):
                    yield from self._runs(choice, deleted)

    def _run_content(self, run: etree._Element) -> Tuple[str, List[etree._Element]]:
        parts: List[str] = []
        boxes: List[etree._Element] = []
        for child in run:
            tag = child.tag
            if tag in (_w("t"), _w("delText")):
                parts.append(child.text or "")
            elif tag in (_w("tab"), _w("ptab")):
                parts.append("\t")
            elif tag in (_w("br"), _w("cr")):
                parts.append("\n")
            elif tag == _w("noBreakHyphen"):
                parts.append("-")
            elif tag in (_w("drawing"), _w("pict"), _w("object"), _mc("AlternateContent")):
                _find_text_boxes(child, boxes)
        return "".join(parts), boxes


def _find_text_boxes(node: etree._Element, out: List[etree._Element]) -> None:
    """Collect <w:txbxContent> elements, skipping the duplicate mc:Fallback copy."""
    for child in node:
        if child.tag == _mc("Fallback"):
            continue
        if child.tag == _w("txbxContent"):
            out.append(child)
        else:
            _find_text_boxes(child, out)


def _natural(name: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def _read_part(zf: zipfile.ZipFile, name: str) -> etree._Element:
    try:
        with zf.open(name) as fh:
            data = fh.read(MAX_DOCX_UNCOMPRESSED_BYTES + 1)
        if len(data) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise NotGraded("DOCX is too large when decompressed")
        return etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise NotGraded(f"DOCX part {name} is not valid XML") from exc


def _crosscheck_text(path: Path) -> str:
    """Body paragraphs and table cells as python-docx sees them."""
    document = docx.Document(str(path))
    parts: List[str] = [p.text for p in document.paragraphs]

    def table_text(table) -> None:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    parts.append(para.text)
                for nested in cell.tables:
                    table_text(nested)

    for table in document.tables:
        table_text(table)
    return "\n".join(parts)


def extract_docx(path: Path) -> ExtractedDocument:
    try:
        zf = zipfile.ZipFile(str(path))
    except (zipfile.BadZipFile, OSError) as exc:
        raise NotGraded(
            "not a valid DOCX file (it may be encrypted, corrupt, or a legacy .doc renamed to .docx)"
        ) from exc

    with zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise NotGraded("not a valid DOCX file (missing document.xml)")
        if sum(i.file_size for i in zf.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise NotGraded("DOCX is too large when decompressed")

        walker = _Walker()
        root = _read_part(zf, "word/document.xml")
        body = root.find(_w("body"))
        if body is None:
            raise NotGraded("not a valid DOCX file (no document body)")
        walker.blocks(body, "body")
        body_span_count = len(walker.spans)

        for part_name, location in (("word/footnotes.xml", "footnote"), ("word/endnotes.xml", "endnote")):
            if part_name in names:
                part = _read_part(zf, part_name)
                for note in part:
                    if note.get(_w("type")) in _SKIPPED_NOTE_TYPES:
                        continue
                    walker.blocks(note, location)
        for prefix, location in (("word/header", "header"), ("word/footer", "footer")):
            for part_name in sorted(
                (n for n in names if re.fullmatch(rf"{prefix}\d*\.xml", n)), key=_natural
            ):
                walker.blocks(_read_part(zf, part_name), location)

        if "word/comments.xml" in names:
            comment_walker = _Walker()
            for comment in _read_part(zf, "word/comments.xml"):
                comment_walker.blocks(comment, "comment")
            for span in comment_walker.spans:
                walker.spans.append(TextSpan(text=span.text, visible=False, location="comment"))

        image_count = sum(1 for n in names if n.startswith("word/media/") and not n.endswith("/"))

    try:
        crosscheck = _crosscheck_text(path)
    except Exception as exc:
        raise NotGraded(f"DOCX could not be read by the cross-check extractor ({type(exc).__name__})") from exc

    body_text = "\n".join(
        s.text for s in walker.spans[:body_span_count]
        if s.visible and (s.location == "body" or s.location.startswith("table"))
    )
    # Spans after the body (notes, headers, footers) never have location "body" or "table N",
    # but text boxes inside the body are excluded from the cross-check scope as well.
    return ExtractedDocument(
        spans=walker.spans,
        page_count=None,
        image_count=image_count,
        extractor=f"sgc-docx (lxml {etree.LXML_VERSION[0]}.{etree.LXML_VERSION[1]})",
        crosscheck_extractor="python-docx",
        crosscheck_text=crosscheck,
        crosscheck_scope_text=body_text,
    )
