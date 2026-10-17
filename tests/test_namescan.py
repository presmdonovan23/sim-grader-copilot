from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from helpers import make_docx, make_pdf, para, prose, text_page

from sgc.namescan import HIGH, LOW, MEDIUM, format_candidates_file, normalize_candidate, scan_directory
from sgc.redact import Redactor, parse_names_text

REPO = Path(__file__).resolve().parent.parent
CORE = (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{}</dc:creator></cp:coreProperties>'
)


def pdf_with(path: Path, *top_lines: str) -> Path:
    body = "\n".join(top_lines)
    page_text = prose(300)

    def first(c):
        c.setFont("Helvetica", 11)
        y = 740
        for line in top_lines:
            c.drawString(50, y, line)
            y -= 16
        text_page(page_text, y=y - 10)(c)

    return make_pdf(path, [first]) if body else make_pdf(path, [text_page(page_text)])


# --- what counts as a name ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Becca Costanza", "Becca Costanza"),
        ("Costanza, Becca", "Becca Costanza"),
        ("BECCA COSTANZA", "Becca Costanza"),
        ("Mary-Ann O'Brien", "Mary-Ann O'Brien"),
        ("Maria de la Cruz", "Maria de la Cruz"),
        ("Jane Q. Public", "Jane Q. Public"),
        ("  Jane   Doe  ", "Jane Doe"),
    ],
)
def test_accepts_name_shapes(text, expected):
    assert normalize_candidate(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Pediatric Nursing",
        "Simulation 1",
        "Part 2: Reflection",
        "Name:",
        "Becca",
        "the patient was",
        "Post Simulation Documentation and Reflection",
        "Fall 2026",
        "08 Oct 2026",
        "jane doe",                       # lowercase prose is not accepted by default
        "Jane.Doe@school.edu",
        "A B",
    ],
)
def test_rejects_non_names(text):
    assert normalize_candidate(text) is None


def test_lowercase_accepted_only_when_asked():
    assert normalize_candidate("jane doe", allow_lower=True) == "Jane Doe"


# --- scanning documents -------------------------------------------------------------------


def test_top_of_document_name_is_found_and_course_title_is_not(tmp_path):
    pdf_with(tmp_path / "a.pdf", "Becca Costanza", "Pediatric Nursing", "08 Oct 2026", "Pediatric Simulation 1")
    result = scan_directory(tmp_path)
    assert [(c.name, c.confidence) for c in result.candidates] == [("Becca Costanza", MEDIUM)]
    assert result.no_name == []


def test_label_is_high_confidence_and_splits_partners(tmp_path):
    make_docx(tmp_path / "a.docx", para("Name: Jane Doe and John Smith    Date: 10/08") + para(prose(300)))
    result = scan_directory(tmp_path)
    assert {c.name: c.confidence for c in result.candidates} == {"Jane Doe": HIGH, "John Smith": HIGH}


def test_metadata_plus_top_line_agree_gives_high(tmp_path):
    make_docx(
        tmp_path / "a.docx",
        para("Doe, Jane") + para(prose(300)),
        parts={"docProps/core.xml": CORE.format("jane doe")},
    )
    (cand,) = scan_directory(tmp_path).candidates
    assert cand.name == "Jane Doe" and cand.confidence == HIGH
    assert {"metadata", "top of document"} <= cand.sources


def test_generic_metadata_authors_are_ignored(tmp_path):
    make_docx(tmp_path / "a.docx", para(prose(300)), parts={"docProps/core.xml": CORE.format("Microsoft Office User")})
    result = scan_directory(tmp_path)
    assert result.candidates == [] and result.no_name == ["a.docx"]


def test_header_text_is_used(tmp_path):
    header = '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Jane Doe</w:t></w:r></w:p></w:hdr>'
    make_docx(tmp_path / "a.docx", para(prose(300)), parts={"word/header1.xml": header})
    (cand,) = scan_directory(tmp_path).candidates
    assert cand.name == "Jane Doe" and "header/footer" in cand.sources


def test_names_in_many_documents_are_treated_as_shared(tmp_path):
    for index, student in enumerate(["Jane Doe", "John Smith", "Ana Lopez", "Wei Zhang"]):
        pdf_with(tmp_path / f"{index}.pdf", student, "Henry Slavin")
    result = scan_directory(tmp_path)
    assert [c.name for c in result.shared] == ["Henry Slavin"]
    assert sorted(c.name for c in result.candidates) == ["Ana Lopez", "Jane Doe", "John Smith", "Wei Zhang"]
    assert all(all(f.name != "Henry Slavin" for f in items) for items in result.per_file.values())


def test_name_only_in_file_name_is_low_confidence(tmp_path):
    pdf_with(tmp_path / "Jane_Doe_Sim1.pdf")
    result = scan_directory(tmp_path)
    (cand,) = result.candidates
    assert (cand.name, cand.confidence) == ("Jane Doe", LOW)
    assert result.no_name == ["Jane_Doe_Sim1.pdf"]


def test_unreadable_and_unsupported_files(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    (tmp_path / "notes.txt").write_text("Jane Doe")
    (tmp_path / "~$temp.docx").write_bytes(b"x")
    result = scan_directory(tmp_path)
    assert list(result.per_file) == ["broken.pdf"] and "broken.pdf" in result.unreadable


def test_candidates_file_marks_low_confidence_as_comments(tmp_path):
    pdf_with(tmp_path / "a.pdf", "Becca Costanza")
    pdf_with(tmp_path / "Jane_Doe_Sim1.pdf")
    text = format_candidates_file(scan_directory(tmp_path))
    lines = text.splitlines()
    assert "Becca Costanza" in lines and "#? Jane Doe" in lines
    # the file is directly usable as a names file: only the confident name is active
    assert parse_names_text(text) == ["Becca Costanza"]
    Redactor(parse_names_text(text))


# --- the script ---------------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("extract_names_script", REPO / "scripts" / "extract_names.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_writes_file_and_refuses_to_overwrite(tmp_path, capsys):
    script = load_script()
    subs = tmp_path / "subs"
    subs.mkdir()
    pdf_with(subs / "a.pdf", "Becca Costanza")
    out = tmp_path / "out.txt"
    assert script.main([str(subs), "-o", str(out)]) == 0
    assert "Becca Costanza" in out.read_text()
    assert "do not commit" in capsys.readouterr().out
    assert script.main([str(subs), "-o", str(out)]) == 2
    assert script.main([str(subs), "-o", str(out), "--force"]) == 0
    assert script.main([str(tmp_path / "missing")]) == 2
    out.unlink()
    assert script.main([str(subs), "--no-file"]) == 0 and not out.exists()
