"""The Grade Recommendation Report (technical spec section 11).

Everything placed in the PDF that did not come from this module is escaped first: student quotes,
model rationales, file names, and reasons are all untrusted. A "Not graded" entry never shows a
score. Prompt-injection flags are shown in a red box at the top of the entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import __version__, config
from .flags import FlagRecord
from .pipeline import STATUS_GRADED, Prepared
from .rubric import Rubric

_FONT_DIR = Path(__file__).parent / "data" / "fonts"
_FONT = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"
_FONT_ITALIC = "DejaVuSans-Oblique"

FLAG_LABELS = {
    "ai_artifact": "AI-generated text artifact",
    "fourth_wall": "Breaks the simulation (fourth wall)",
    "profanity": "Profanity",
    "unrelated_text": "Unrelated text",
    "prompt_injection": "Possible prompt injection",
    "patient_identifier": "Possible patient identifier",
    "real_patient_care": "Describes real patient care",
}

STATUS_LABEL_GRADED = "Graded"
STATUS_LABEL_FLAGS = "Graded with flags"
STATUS_LABEL_NOT_GRADED = "Not graded"

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_RED = colors.HexColor("#b00020")
_RED_BG = colors.HexColor("#fdecea")
_AMBER_BG = colors.HexColor("#fff4e0")
_GREY = colors.HexColor("#555555")
_HEAD_BG = colors.HexColor("#e8eaf0")
_BANNER_BG = colors.HexColor("#1f2d4d")


@dataclass
class ReportInfo:
    started: datetime
    model: str
    rubric: Rubric
    rubric_path: str
    usage: Optional[dict] = None


def _register_fonts() -> None:
    if _FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(_FONT, str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_ITALIC, str(_FONT_DIR / "DejaVuSans-Oblique.ttf")))
    pdfmetrics.registerFontFamily(
        _FONT, normal=_FONT, bold=_FONT_BOLD, italic=_FONT_ITALIC, boldItalic=_FONT_BOLD
    )


def _esc(text: object) -> str:
    """Escape untrusted text for a reportlab Paragraph; line breaks become <br/>."""
    cleaned = _CONTROL_CHARS.sub("", str(text))
    return escape(cleaned).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName=_FONT, fontSize=9.5, leading=13)
    return {
        "body": body,
        "small": ParagraphStyle("small", parent=body, fontSize=8, leading=10.5),
        "muted": ParagraphStyle("muted", parent=body, fontSize=8.5, leading=11, textColor=_GREY),
        "title": ParagraphStyle("title", parent=body, fontName=_FONT_BOLD, fontSize=18, leading=22, spaceAfter=6),
        "h1": ParagraphStyle(
            "h1", parent=body, fontName=_FONT_BOLD, fontSize=13, leading=16, spaceBefore=10, spaceAfter=4
        ),
        "h2": ParagraphStyle(
            "h2", parent=body, fontName=_FONT_BOLD, fontSize=10.5, leading=13, spaceBefore=7, spaceAfter=2
        ),
        "quote": ParagraphStyle(
            "quote", parent=body, fontName=_FONT_ITALIC, fontSize=9, leading=12, leftIndent=14, textColor=_GREY
        ),
        "cell": ParagraphStyle("cell", parent=body, fontSize=8, leading=10),
        "cellc": ParagraphStyle("cellc", parent=body, fontSize=8, leading=10, alignment=TA_CENTER),
        "head": ParagraphStyle("head", parent=body, fontName=_FONT_BOLD, fontSize=7.5, leading=9, alignment=TA_CENTER),
        "alert": ParagraphStyle("alert", parent=body, fontSize=9, leading=12),
        "banner": ParagraphStyle(
            "banner", parent=body, fontName=_FONT_BOLD, fontSize=17, leading=21, textColor=colors.white
        ),
        "banner_sub": ParagraphStyle(
            "banner_sub", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#c9d1e6")
        ),
    }


# --- classification ------------------------------------------------------------------------


def status_label(item: Prepared) -> str:
    if item.status != STATUS_GRADED or item.grade is None:
        return STATUS_LABEL_NOT_GRADED
    return STATUS_LABEL_FLAGS if item.flags else STATUS_LABEL_GRADED


def word_count_text(item: Prepared) -> str:
    if item.word_count is None:
        return ""
    if item.word_count_flag == "under_min":
        return f"{item.word_count} words (under {config.WORD_COUNT_MIN})"
    if item.word_count_flag == "over_max":
        return f"{item.word_count} words (over {config.WORD_COUNT_MAX})"
    return f"{item.word_count} words (within {config.WORD_COUNT_MIN}-{config.WORD_COUNT_MAX})"


def _is_injection(flag: FlagRecord) -> bool:
    return flag.type == "prompt_injection"


# --- pieces --------------------------------------------------------------------------------


def _summary_page(items: List[Prepared], info: ReportInfo, st: Dict[str, ParagraphStyle]) -> list:
    graded = [i for i in items if status_label(i) == STATUS_LABEL_GRADED]
    flagged = [i for i in items if status_label(i) == STATUS_LABEL_FLAGS]
    not_graded = [i for i in items if status_label(i) == STATUS_LABEL_NOT_GRADED]
    categories = info.rubric.categories

    story: list = [Paragraph("Grade Recommendation Report", st["title"])]
    story.append(Paragraph("Recommendations only. The instructor makes the final grading decisions.", st["muted"]))
    story.append(Spacer(1, 6))
    meta = [
        ("Run", info.started.strftime("%Y-%m-%d %H:%M:%S")),
        ("SGC version", __version__),
        ("Model", info.model),
        ("Rubric", f"{info.rubric.title} ({Path(info.rubric_path).name})"),
        ("Rubric sha256", info.rubric.sha256 or "n/a"),
        ("Submissions", str(len(items))),
        (
            "Outcome",
            f"{len(graded)} graded, {len(flagged)} graded with flags, {len(not_graded)} not graded",
        ),
    ]
    if info.usage and info.usage.get("estimated_cost_usd") is not None:
        meta.append(("Estimated cost", f"${info.usage['estimated_cost_usd']:.2f}"))
    meta_table = Table(
        [[Paragraph(f"<b>{_esc(k)}</b>", st["small"]), Paragraph(_esc(v), st["small"])] for k, v in meta],
        colWidths=[1.2 * inch, 5.8 * inch],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    header = ["File", "Total"] + [f"C{n}" for n in range(1, len(categories) + 1)] + ["Words", "Length", "Flags", "Borderline", "Status"]
    rows = [[Paragraph(_esc(h), st["head"]) for h in header]]
    for item in items:
        scored = status_label(item) != STATUS_LABEL_NOT_GRADED and item.grade is not None
        row = [Paragraph(_esc(item.filename), st["cell"])]
        if scored:
            grade = item.grade
            row.append(Paragraph(f"{grade.total}/{grade.max_total}", st["cellc"]))
            row.extend(Paragraph(str(c.points), st["cellc"]) for c in grade.categories)
        else:
            row.append(Paragraph("-", st["cellc"]))
            row.extend(Paragraph("-", st["cellc"]) for _ in categories)
        length = {"under_min": "Under", "over_max": "Over"}.get(item.word_count_flag or "", "OK" if scored else "-")
        row.append(Paragraph("-" if item.word_count is None else str(item.word_count), st["cellc"]))
        row.append(Paragraph(length, st["cellc"]))
        row.append(Paragraph("Yes" if item.flags else "-", st["cellc"]))
        row.append(Paragraph("Yes" if scored and item.grade.borderline_categories else "-", st["cellc"]))
        row.append(Paragraph(_esc(status_label(item)), st["cell"]))
        rows.append(row)

    total_width = 7.0 * inch
    fixed = [0.55, *([0.38] * len(categories)), 0.6, 0.6, 0.55, 0.8, 0.85]
    file_width = max(1.2, 7.0 - sum(fixed))
    widths = [w * inch for w in [file_width, *fixed]]
    scale = total_width / sum(widths)
    table = Table(rows, colWidths=[w * scale for w in widths], repeatRows=1)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8bcc8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for index, item in enumerate(items, start=1):
        label = status_label(item)
        if label == STATUS_LABEL_NOT_GRADED:
            table_style.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#eeeeee")))
        elif any(_is_injection(f) for f in item.flags):
            table_style.append(("BACKGROUND", (0, index), (-1, index), _RED_BG))
        elif item.flags:
            table_style.append(("BACKGROUND", (0, index), (-1, index), _AMBER_BG))
    table.setStyle(TableStyle(table_style))
    story.append(table)
    story.append(Spacer(1, 6))
    legend = "; ".join(f"C{n} = {c.name} (max {c.max_points})" for n, c in enumerate(categories, start=1))
    story.append(Paragraph(_esc(legend), st["muted"]))
    story.append(
        Paragraph(
            "Length: Under = fewer than "
            f"{config.WORD_COUNT_MIN} words, Over = more than {config.WORD_COUNT_MAX}. "
            "Rows shaded red contain a possible prompt injection; amber rows have other flags; grey rows were not graded.",
            st["muted"],
        )
    )
    story.append(
        Paragraph(
            "This report contains file names and student work excerpts. Treat it as confidential.",
            st["muted"],
        )
    )
    return story


def _alert_box(flags: List[FlagRecord], st: Dict[str, ParagraphStyle]) -> Table:
    rows = [[Paragraph(
        "<b>POSSIBLE PROMPT INJECTION.</b> The submission contains text that appears to address the "
        "grader. It was neutralized before grading, but review the original document.",
        st["alert"],
    )]]
    for flag in flags:
        where = " (hidden text)" if flag.in_hidden else ""
        rows.append([Paragraph(
            f"<i>{_esc(flag.quote)}</i>{_esc(where)}<br/>{_esc(flag.explanation)}", st["alert"]
        )])
    box = Table(rows, colWidths=[7.0 * inch])
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.5, _RED),
                ("BACKGROUND", (0, 0), (-1, -1), _RED_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return box


def _flag_list(flags: List[FlagRecord], st: Dict[str, ParagraphStyle]) -> list:
    out: list = [Paragraph("Suspicious activity", st["h2"])]
    for flag in flags:
        label = FLAG_LABELS.get(flag.type, flag.type)
        notes = []
        if flag.in_hidden:
            notes.append("found in hidden text")
        if not flag.located:
            notes.append("quote could not be located in the text")
        suffix = f" ({', '.join(notes)})" if notes else ""
        out.append(Paragraph(f"<b>{_esc(label)}</b>{_esc(suffix)}", st["body"]))
        if flag.quote:
            out.append(Paragraph(f"\u201c{_esc(flag.quote)}\u201d", st["quote"]))
        out.append(Paragraph(_esc(flag.explanation), st["body"]))
        out.append(Spacer(1, 3))
    return out


def _banner(item: Prepared, number: int, count: int, st: Dict[str, ParagraphStyle]) -> Table:
    banner = Table(
        [
            [Paragraph(f"SUBMISSION {number} OF {count}", st["banner_sub"])],
            [Paragraph(_esc(item.filename), st["banner"])],
        ],
        colWidths=[7.0 * inch],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BANNER_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                ("TOPPADDING", (0, 1), (-1, 1), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ]
        )
    )
    return banner


def _submission_section(item: Prepared, number: int, count: int, st: Dict[str, ParagraphStyle]) -> list:
    label = status_label(item)
    story: list = [PageBreak(), _banner(item, number, count, st), Spacer(1, 6)]
    story.append(Paragraph(f"<b>Status:</b> {_esc(label)}", st["body"]))
    injection = [f for f in item.flags if _is_injection(f)]
    others = [f for f in item.flags if not _is_injection(f)]

    if label == STATUS_LABEL_NOT_GRADED:
        story.append(Spacer(1, 3))
        story.append(Paragraph(f"<b>Reason:</b> {_esc(item.reason or 'unknown')}", st["body"]))
        if injection:  # an injection attempt is always surfaced, even when nothing was graded
            story.append(Spacer(1, 4))
            story.append(_alert_box(injection, st))
        return story

    grade = item.grade
    assert grade is not None
    if injection:
        story.append(Spacer(1, 4))
        story.append(_alert_box(injection, st))
    story.append(Paragraph(f"<b>Total: {grade.total}/{grade.max_total}</b>", st["h2"]))
    for result in grade.categories:
        block: list = [
            Paragraph(f"{_esc(result.category)}: {result.points}/{result.max_points}", st["h2"]),
            Paragraph(_esc(result.rationale), st["body"]),
        ]
        for quote in result.quotes:
            block.append(Paragraph(f"\u201c{_esc(quote)}\u201d", st["quote"]))
        if result.borderline:
            alt = f"{result.alternative_points}/{result.max_points}" if result.alternative_points is not None else "n/a"
            reason = f" {_esc(result.borderline_reason)}" if result.borderline_reason else ""
            block.append(
                Paragraph(f"<b>Borderline.</b> Could also be {_esc(alt)}.{reason}", st["body"])
            )
        story.append(KeepTogether(block[:3]))
        story.extend(block[3:])

    story.append(Paragraph("Length", st["h2"]))
    story.append(Paragraph(_esc(word_count_text(item)), st["body"]))
    if others:
        story.extend(_flag_list(others, st))
    if grade.total == grade.max_total:
        story.append(Paragraph("Suggestion for improvement (perfect score)", st["h2"]))
        if grade.weakest:
            weak = grade.weakest
            story.append(Paragraph(f"<b>{_esc(weak.category)}.</b> {_esc(weak.explanation)}", st["body"]))
            story.append(Paragraph(f"\u201c{_esc(weak.quote)}\u201d", st["quote"]))
            story.append(Paragraph(_esc(weak.suggestion), st["body"]))
        else:
            story.append(Paragraph(_esc(grade.weakest_note or "No suggestion available."), st["body"]))
    story.append(Paragraph("De-identification", st["h2"]))
    story.append(Paragraph(f"{item.replacements} name replacement(s) made before grading.", st["body"]))
    notes = list(item.warnings)
    if item.hidden:
        notes.append(f"hidden text found: {len(item.hidden)} item(s)")
    if grade.samples_valid < grade.samples_requested:
        notes.append(f"only {grade.samples_valid} of {grade.samples_requested} grading samples were valid")
    if notes:
        story.append(Paragraph("Notes", st["h2"]))
        for note in notes:
            story.append(Paragraph(f"\u2022 {_esc(note)}", st["body"]))
    return story


def _footer(canvas, doc) -> None:  # pragma: no cover - drawing only
    canvas.saveState()
    canvas.setFont(_FONT, 7.5)
    canvas.setFillColor(_GREY)
    canvas.drawString(0.75 * inch, 0.45 * inch, "Sim Grader Copilot: confidential, contains student work")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_report(path: Path, items: List[Prepared], info: ReportInfo) -> None:
    """Write the PDF report to `path`. A partial file is removed if writing fails."""
    _register_fonts()
    st = _styles()
    ordered = sorted(items, key=lambda i: (i.filename.casefold(), i.filename))
    story = _summary_page(ordered, info, st)
    for number, item in enumerate(ordered, start=1):
        story.extend(_submission_section(item, number, len(ordered), st))
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.8 * inch,
        title="Grade Recommendation Report",
        author=f"Sim Grader Copilot {__version__}",
    )
    try:
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    except Exception:
        path.unlink(missing_ok=True)
        raise
