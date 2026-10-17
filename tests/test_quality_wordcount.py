from __future__ import annotations

import pytest

from sgc.errors import NotGraded
from sgc.extract.quality import check_quality
from sgc.extract.types import ExtractedDocument, TextSpan
from sgc.wordcount import count_words, word_count_flag

GOOD = (
    "In the simulation I was the primary nurse and I learned that it is important to stay calm "
    "and to communicate with the team because the patient was getting worse and we had to act. "
) * 4


def doc(text: str, **kwargs) -> ExtractedDocument:
    return ExtractedDocument(
        spans=[TextSpan(text=text, visible=True, location="body")], extractor="test", **kwargs
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        ("   \n\t ", 0),
        ("one two three", 3),
        ("well-being counts once", 3),
        ("a - b -- c", 3),          # punctuation-only tokens are not words
        ("... !!! ???", 0),
        ("in 2024 there were 3 cases", 6),
        ("tab\tseparated\nand\nlines", 4),
        ("it's fine", 2),
    ],
)
def test_count_words(text, expected):
    assert count_words(text) == expected


@pytest.mark.parametrize(
    "count, flag",
    [(0, "under_min"), (499, "under_min"), (500, None), (750, None), (1000, None), (1001, "over_max")],
)
def test_word_count_flag_boundaries(count, flag):
    assert word_count_flag(count) == flag


def test_good_text_passes():
    assert check_quality(doc(GOOD)) == count_words(GOOD)


def test_empty_is_not_graded():
    with pytest.raises(NotGraded, match="no text"):
        check_quality(doc("   "))


def test_garbled_text_is_not_graded():
    garbled = ("\ufffd\ufffd\u00e9\ufffd " * 80)
    with pytest.raises(NotGraded, match="garbled"):
        check_quality(doc(garbled))


def test_private_use_characters_are_not_graded():
    text = GOOD + ("\ue000" * 20)
    with pytest.raises(NotGraded, match="garbled"):
        check_quality(doc(text))


def test_non_prose_is_not_graded():
    text = " ".join(f"xq{n}zv" for n in range(200))
    with pytest.raises(NotGraded, match="readable English"):
        check_quality(doc(text))


def test_too_long_is_not_graded():
    with pytest.raises(NotGraded, match="too long"):
        check_quality(doc(GOOD * 400))


def test_extractors_disagree_on_count():
    d = doc(GOOD, crosscheck_extractor="other", crosscheck_text=GOOD + " " + GOOD, crosscheck_scope_text=GOOD)
    with pytest.raises(NotGraded, match="disagree"):
        check_quality(d)


def test_extractors_disagree_on_content():
    other = GOOD.replace("simulation", "simulatXon").replace("nurse", "nurze").replace("calm", "calx")
    d = doc(GOOD, crosscheck_extractor="other", crosscheck_text=other, crosscheck_scope_text=GOOD)
    with pytest.raises(NotGraded, match="disagree"):
        check_quality(d)


def test_extractors_agree_within_tolerance():
    d = doc(GOOD, crosscheck_extractor="other", crosscheck_text=GOOD.replace("simulation", "simulation ."),
            crosscheck_scope_text=GOOD)
    check_quality(d)


def test_ligature_differences_are_ignored():
    text = GOOD + " The finest first office."
    other = GOOD + " The \ufb01nest \ufb01rst o\ufb03ce."
    d = doc(text, crosscheck_extractor="other", crosscheck_text=other, crosscheck_scope_text=text)
    check_quality(d)
