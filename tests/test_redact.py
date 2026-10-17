from __future__ import annotations

import pytest

from sgc.errors import NamesFileError, NotGraded
from sgc.redact import (
    RedactedText,
    Redactor,
    RedactionVerificationError,
    Verifier,
    finalize,
    parse_names,
    parse_names_text,
)


def red(names, text):
    return Redactor(names).redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "Susan Lee", "SUSAN LEE", "susan lee", "Lee, Susan", "Lee,Susan", "Lee Susan",
        "S. Lee", "S Lee", "S.Lee", "Susan L.", "Susan L", "Susan", "Lee", "Lee, S.",
        "Susan\nLee", "Susan   Lee", "Susan\u00a0Lee",
    ],
)
def test_susan_lee_variants(text):
    result = red(["Susan Lee"], f"I worked with {text} today.")
    assert result.text == "I worked with [NAME] today."
    assert result.replacements == 1


def test_full_name_is_one_replacement():
    result = red(["Susan Lee"], "Susan Lee told me. Then Lee left.")
    assert result.text == "[NAME] told me. Then [NAME] left."
    assert result.replacements == 2


@pytest.mark.parametrize("text", ["Susan's", "Susan\u2019s", "Lees'", "Lee's"])
def test_possessives_are_included(text):
    result = red(["Susan Lee"], f"That is {text} chart.")
    assert result.text == "That is [NAME] chart."


def test_names_inside_larger_words_are_not_matched():
    text = "We drove to Leeds and saw Leeward islands and Susannah."
    result = red(["Susan Lee"], text)
    assert result.text == text
    assert result.replacements == 0


def test_common_word_names_are_always_replaced():
    result = red(["Hope"], "I hope so. Hope is nice.")
    assert result.text == "I [NAME] so. [NAME] is nice."
    assert result.replacements == 2


def test_accents_and_case():
    assert red(["Jose Garcia"], "JOSÉ GARCÍA and José").text == "[NAME] and [NAME]"
    assert red(["José García"], "jose garcia").text == "[NAME]"
    assert red(["Zoe"], "Zoë was there").text == "[NAME] was there"


def test_decomposed_accents():
    assert red(["Jose Garcia"], "Jose\u0301 Garci\u0301a").text == "[NAME]"


def test_zero_width_and_soft_hyphens_removed():
    result = red(["Susan Lee"], "Su\u200bsan Le\u00ade here")
    assert result.text == "[NAME] here"


def test_line_break_hyphenation():
    result = red(["Susan Lee"], "I talked to Sus-\nan about it.")
    assert result.text == "I talked to [NAME] about it."


def test_hyphen_before_line_break_kept_when_next_word_is_capitalised():
    result = red(["Nobody"], "Smith-\nJones agreed")
    assert result.text == "Smith-\nJones agreed"


def test_three_part_name():
    names = ["Mary Ann Smith"]
    for text in ["Mary Ann Smith", "Mary Smith", "Smith, Mary Ann", "Smith, Mary", "M. A. Smith",
                 "M. Smith", "Mary", "Ann", "Smith", "Mary Ann S."]:
        assert red(names, f"x {text} y").text == "x [NAME] y", text


def test_short_token_names_only_use_full_forms():
    names = ["Wei Li"]
    assert red(names, "Wei Li and Li, Wei and Li Wei").text == "[NAME] and [NAME] and [NAME]"
    assert red(names, "Wei").text == "[NAME]"             # the long token is still removed alone
    text = "The li ion battery and a Li element"
    assert red(names, text).text == text                  # "Li" alone is never removed
    assert red(names, "W. Li").text == "W. Li"            # no initial-based variants for short tokens


def test_honorific_names():
    names = ["Dr. Patel"]
    assert red(names, "Dr. Patel said").text == "[NAME] said"
    assert red(names, "dr patel said").text == "[NAME] said"
    assert red(names, "Patel said").text == "[NAME] said"
    assert red(["Dr. Susan Lee"], "Dr Susan Lee and Susan and Lee").text == "[NAME] and [NAME] and [NAME]"


def test_hyphenated_names():
    names = ["Smith-Jones"]
    for text in ["Smith-Jones", "Smith\u2013Jones", "Smith Jones", "Smith", "Jones", "Smith - Jones"]:
        assert red(names, f"x {text} y").text == "x [NAME] y", text


def test_apostrophe_names():
    names = ["Pat O'Brien"]
    assert red(names, "Pat O\u2019Brien and O'Brien and Pat").text == "[NAME] and [NAME] and [NAME]"


def test_multiple_names():
    names = ["John Doe", "Jane Smith", "Todd Baker"]
    result = red(names, "John and Jane Smith met todd BAKER and Doe.")
    assert result.text == "[NAME] and [NAME] met [NAME] and [NAME]."
    assert result.replacements == 4


def test_no_names_present():
    result = red(["Susan Lee"], "The patient was stable.")
    assert result.text == "The patient was stable."
    assert result.replacements == 0


# --- verification fails closed ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["S u s a n  L e e", "S.u.s.a.n L.e.e", "SusanLee", "Susan_Lee", "L e e S u s a n"],
)
def test_spaced_out_or_obfuscated_names_fail_closed(text):
    with pytest.raises(RedactionVerificationError):
        red(["Susan Lee"], f"I met {text} today")


def test_invisible_characters_inside_name_are_removed():
    assert red(["Susan Lee"], "I met Susan\u200b\u2060 Lee today").text == "I met [NAME] today"


def test_verification_error_is_not_graded():
    with pytest.raises(NotGraded, match="remained"):
        red(["Susan Lee"], "S u s a n  L e e")


def test_finalize_rejects_surviving_name():
    verifier = Verifier(["Susan Lee"])
    with pytest.raises(RedactionVerificationError):
        finalize("Hello Susan", verifier)
    with pytest.raises(RedactionVerificationError):
        finalize("Hello s u s a n l e e", verifier)
    finalize("Hello [NAME]", verifier)


def test_verifier_is_stricter_than_matcher():
    # Plural forms are not matched by the redactor but "lee" alone as a token is.
    verifier = Verifier(["Susan Lee"])
    assert verifier.survivors("the lee, the")
    assert not verifier.survivors("the leeds and the leeway")


# --- type guard ---------------------------------------------------------------------------


def test_redacted_text_cannot_be_constructed_directly():
    with pytest.raises(TypeError):
        RedactedText("Susan Lee")
    with pytest.raises(TypeError):
        RedactedText("Susan Lee", object())


def test_redactor_returns_redacted_text_type():
    result = red(["Susan Lee"], "hello Susan")
    assert isinstance(result.text, RedactedText)
    assert isinstance(result.text, str)


# --- names file ---------------------------------------------------------------------------


def test_parse_names_text():
    raw = "\ufeff# comment\n\n  Susan   Lee \nsusan lee\nJohn Doe\n   \n# another\nJOHN DOE\n"
    assert parse_names_text(raw) == ["Susan Lee", "John Doe"]


def test_parse_names_empty():
    with pytest.raises(NamesFileError, match="no names"):
        parse_names_text("# only a comment\n\n")


def test_parse_names_file(tmp_path):
    f = tmp_path / "names.txt"
    f.write_text("Susan Lee\n", encoding="utf-8")
    assert parse_names(f) == ["Susan Lee"]
    with pytest.raises(NamesFileError, match="Cannot read"):
        parse_names(tmp_path / "missing.txt")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(NamesFileError, match="UTF-8"):
        parse_names(bad)
