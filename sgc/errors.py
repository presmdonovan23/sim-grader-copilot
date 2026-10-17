"""Exception types shared across SGC."""

from __future__ import annotations


class FatalError(Exception):
    """A problem that stops the whole run before any submission is processed (exit code 2)."""


class RubricError(FatalError):
    """The rubric file is missing or invalid."""


class NamesFileError(FatalError):
    """The names file is missing, unreadable, or contains no names."""


class NotGraded(Exception):
    """A single submission cannot be graded. The message is the reason shown to the instructor."""
