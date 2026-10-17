"""Call a model stage and retry when its output is invalid (technical spec section 10.4)."""

from __future__ import annotations

from typing import Callable, Type, TypeVar

from pydantic import BaseModel

from ..config import MAX_VALIDATION_ATTEMPTS
from ..runlog import get_logger
from .base import Fixed, LLMClient, RedactedPrompt
from .errors import InvalidModelOutput, ValidationFailure

M = TypeVar("M", bound=BaseModel)
R = TypeVar("R")


def call_validated(
    llm: LLMClient,
    *,
    stage: str,
    sid: str,
    system: Fixed,
    make_prompt: Callable[[bool], RedactedPrompt],
    schema: Type[M],
    validate: Callable[[M], R],
    max_attempts: int = MAX_VALIDATION_ATTEMPTS,
) -> R:
    """One call plus retries. `make_prompt(retry)` builds the prompt; `validate` raises
    `ValidationFailure` for output that parses but is unacceptable. Network errors, spend limits,
    and auth errors are not retried here."""
    last = "no attempts made"
    for attempt in range(1, max_attempts + 1):
        try:
            parsed = llm.complete_structured(
                stage=stage,
                submission_id=sid,
                attempt=attempt,
                system=system,
                user=make_prompt(attempt > 1),
                schema=schema,
            )
            return validate(parsed)
        except (InvalidModelOutput, ValidationFailure) as exc:
            last = str(exc)
            get_logger().warning("%s %s attempt %d rejected: %s", sid, stage, attempt, last)
    raise ValidationFailure(f"{stage} output was still invalid after {max_attempts} attempts ({last})")
