"""Parse and validate the rubric Markdown file (technical spec section 5).

Expected format:

    # Title
    Total Points: 16

    ## Category Name (4 points)

    - **Level Name (4):** description
    - **Another Level (3):** description
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List

from pydantic import BaseModel

from .errors import RubricError


class Level(BaseModel):
    name: str
    points: int
    description: str


class Category(BaseModel):
    name: str
    max_points: int
    levels: List[Level]


class Rubric(BaseModel):
    title: str
    total_points: int
    categories: List[Category]
    source_path: str
    sha256: str

    @property
    def perfect_score(self) -> int:
        return sum(c.max_points for c in self.categories)


_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
_TOTAL_RE = re.compile(r"^\s*Total Points:\s*(\d+)\s*$", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"^##\s+(.+?)\s*\(\s*(\d+)\s+points?\s*\)\s*$", re.IGNORECASE)
_LEVEL_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?)\s*\(\s*(\d+)\s*\):?\s*\*\*:?\s*(.*?)\s*$")
_LEVEL_LIKE_RE = re.compile(r"^\s*[-*]\s+\*\*")


def load_rubric(path: Path) -> Rubric:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RubricError(f"Cannot read rubric file {path}: {exc.strerror or exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RubricError(f"Rubric file {path} is not valid UTF-8") from exc
    return parse_rubric(text, source_path=str(path), sha256=hashlib.sha256(raw).hexdigest())


def parse_rubric(text: str, *, source_path: str = "<memory>", sha256: str = "") -> Rubric:
    title = ""
    total_points = None
    categories: List[Category] = []
    current = None  # type: ignore[assignment]

    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            m = _CATEGORY_RE.match(line)
            if not m:
                raise RubricError(
                    f"Rubric line {lineno}: category heading must look like "
                    f"'## Name (N points)': {line.strip()!r}"
                )
            current = Category(name=m.group(1), max_points=int(m.group(2)), levels=[])
            categories.append(current)
            continue
        if line.startswith("# ") and not title:
            title = _TITLE_RE.match(line).group(1)  # type: ignore[union-attr]
            continue
        m = _TOTAL_RE.match(line)
        if m:
            total_points = int(m.group(1))
            continue
        if _LEVEL_LIKE_RE.match(line):
            m = _LEVEL_RE.match(line)
            if not m:
                raise RubricError(
                    f"Rubric line {lineno}: level must look like "
                    f"'- **Name (points):** description': {line.strip()!r}"
                )
            if current is None:
                raise RubricError(f"Rubric line {lineno}: level appears before any category")
            current.levels.append(
                Level(name=m.group(1), points=int(m.group(2)), description=m.group(3))
            )

    if total_points is None:
        raise RubricError("Rubric has no 'Total Points: N' line")
    if not categories:
        raise RubricError("Rubric has no categories")

    for cat in categories:
        if not cat.levels:
            raise RubricError(f"Category {cat.name!r} has no levels")
        points = [lvl.points for lvl in cat.levels]
        if len(set(points)) != len(points):
            raise RubricError(f"Category {cat.name!r} has duplicate point values: {points}")
        if max(points) != cat.max_points:
            raise RubricError(
                f"Category {cat.name!r}: top level is {max(points)} points but the category "
                f"maximum is {cat.max_points}"
            )
        if min(points) < 0:
            raise RubricError(f"Category {cat.name!r} has a negative point value")
        cat.levels.sort(key=lambda lvl: -lvl.points)

    names = [c.name for c in categories]
    if len(set(names)) != len(names):
        raise RubricError("Rubric has duplicate category names")

    category_sum = sum(c.max_points for c in categories)
    if category_sum != total_points:
        raise RubricError(
            f"Category maximums add up to {category_sum} but the rubric says "
            f"Total Points: {total_points}"
        )

    return Rubric(
        title=title,
        total_points=total_points,
        categories=categories,
        source_path=source_path,
        sha256=sha256,
    )
