"""Helpers that generate PDF and DOCX fixtures for tests."""

from __future__ import annotations

import io
import textwrap
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

SENTENCES = (
    "In the simulation I was the primary nurse for a patient with chest pain and I learned that "
    "it is important to stay calm and to communicate with the team. During the debriefing my "
    "classmates told me that I should have asked for help sooner and I agree with them because "
    "the patient was getting worse. Next time I will use the checklist from the course and I will "
    "review the evidence about early recognition of deterioration before the next lab."
)


def prose(n_words: int) -> str:
    words = SENTENCES.split()
    out: List[str] = []
    while len(out) < n_words:
        out.extend(words)
    return " ".join(out[:n_words])


def make_pdf(
    path: Path,
    pages: List[Callable[[canvas.Canvas], None]],
) -> Path:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for draw in pages:
        draw(c)
        c.showPage()
    c.save()
    return path


def text_page(text: str, *, font_size: int = 11, y: float = 740) -> Callable[[canvas.Canvas], None]:
    def draw(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", font_size)
        yy = y
        for line in textwrap.wrap(text, 90):
            c.drawString(50, yy, line)
            yy -= font_size + 3
    return draw


def simple_pdf(path: Path, words: int = 600, pages: int = 2) -> Path:
    per_page = words // pages
    return make_pdf(path, [text_page(prose(per_page)) for _ in range(pages)])


# --- DOCX ---------------------------------------------------------------------------------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NAMESPACES = (
    f'xmlns:w="{W_NS}" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)


def para(text: str, rpr: str = "") -> str:
    props = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f'<w:p><w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def make_docx(
    path: Path,
    body: str,
    parts: Optional[Dict[str, str]] = None,
    media: Optional[List[str]] = None,
) -> Path:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {NAMESPACES}><w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("word/document.xml", document)
        for name, xml in (parts or {}).items():
            zf.writestr(
                name,
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml,
            )
        for name in media or []:
            zf.writestr(f"word/media/{name}", b"\x89PNG fake")
    return path


def wrap_part(root: str, inner: str) -> str:
    return f"<w:{root} {NAMESPACES}>{inner}</w:{root}>"


def image_pdf(path: Path) -> Path:
    from PIL import Image

    img = Image.new("RGB", (200, 200), (120, 120, 120))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    reader = ImageReader(buf)

    def draw(c: canvas.Canvas) -> None:
        c.drawImage(reader, 50, 100, width=500, height=600)

    return make_pdf(path, [draw])
