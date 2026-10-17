from __future__ import annotations

from pathlib import Path

import pytest

from sgc.errors import RubricError
from sgc.rubric import load_rubric, parse_rubric

REPO = Path(__file__).resolve().parent.parent

VALID = """# Test Rubric

Total Points: 8

## Alpha (4 points)

- **Great (4):** very good
- **Fine (3):** fine
- **Poor (2):** poor
- **Bad (1):** bad
- **None (0):** nothing

## Beta (4 points)

- **Great (4):** very good
- **None (0):** nothing
"""


def test_real_rubric_parses():
    rubric = load_rubric(REPO / "rubric" / "simulation_reflection_rubric.md")
    assert rubric.total_points == 16
    assert [c.name for c in rubric.categories] == [
        "Depth of Insight",
        "Self-Awareness",
        "Clinical Judgment & Decision-Making",
        "Takeaways from Peer Feedback & Debriefing",
    ]
    assert all(len(c.levels) == 5 for c in rubric.categories)
    assert rubric.perfect_score == 16
    assert len(rubric.sha256) == 64


def test_valid_minimal():
    rubric = parse_rubric(VALID)
    assert rubric.title == "Test Rubric"
    assert [lvl.points for lvl in rubric.categories[0].levels] == [4, 3, 2, 1, 0]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda t: t.replace("Total Points: 8\n", ""), "Total Points"),
        (lambda t: t.replace("Total Points: 8", "Total Points: 9"), "add up"),
        (lambda t: t.replace("- **Fine (3):** fine", "- **Fine (4):** fine"), "duplicate"),
        (lambda t: t.replace("- **Great (4):** very good\n- **Fine", "- **Great (3):** very good\n- **Fine", 1)
         .replace("- **Fine (3)", "- **Fine (2)", 1), "duplicate"),
        (lambda t: t.replace("## Alpha (4 points)", "## Alpha"), "category heading"),
        (lambda t: t.replace("- **Great (4):** very good\n- **Fine (3):** fine\n- **Poor (2):** poor\n- **Bad (1):** bad\n- **None (0):** nothing", ""), "no levels"),
        (lambda t: t.replace("- **Poor (2):** poor", "- **Poor:** poor"), "level must look like"),
        (lambda t: t.replace("## Beta", "## Alpha").replace("Alpha (4 points)\n\n- **Great (4):** very good\n- **None", "Alpha (4 points)\n\n- **Great (4):** very good\n- **None"), "duplicate category"),
    ],
)
def test_invalid_rubrics(mutation, message):
    with pytest.raises(RubricError, match=message):
        parse_rubric(mutation(VALID))


def test_top_level_must_equal_max():
    text = VALID.replace("## Beta (4 points)", "## Beta (3 points)").replace("Total Points: 8", "Total Points: 7")
    with pytest.raises(RubricError, match="top level"):
        parse_rubric(text)


def test_no_categories():
    with pytest.raises(RubricError, match="no categories"):
        parse_rubric("# T\n\nTotal Points: 4\n")


def test_missing_file(tmp_path):
    with pytest.raises(RubricError, match="Cannot read"):
        load_rubric(tmp_path / "nope.md")
