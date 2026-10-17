"""Errors raised by the model client and the model-stage code."""

from __future__ import annotations

from typing import Optional

from ..errors import FatalError


class ModelError(Exception):
    """A model call failed in a way that makes the current submission Not graded."""


class ModelUnavailable(ModelError):
    """The model could not be reached or rejected the request after all retries."""


class SpendLimitReached(ModelError):
    """The next call would exceed --max-spend."""


class InvalidModelOutput(ModelError):
    """The call succeeded but the output did not parse against the schema."""

    def __init__(self, message: str, raw: Optional[str] = None) -> None:
        super().__init__(message)
        self.raw = raw


class ValidationFailure(ModelError):
    """The output parsed but failed a content check (rubric levels, quotes, and so on)."""


class ModelAuthError(FatalError):
    """Bad API key, no access, or no remaining quota: every later call would fail too."""
