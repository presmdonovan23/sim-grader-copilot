"""The client interface and the prompt type guard.

Every prompt is assembled from `Fixed` constants and `RedactedText`; a plain `str` is rejected, so
text that has not been through redaction cannot be sent to a model.
"""

from __future__ import annotations

from typing import Iterable, List, Protocol, Tuple, Type, TypeVar

from pydantic import BaseModel

from ..redact import RedactedText

T = TypeVar("T", bound=BaseModel)


class Fixed(str):
    """Trusted constant text written by the developers (instructions, the rubric)."""


class RedactedPrompt:
    """A prompt made only of `Fixed` and `RedactedText` segments."""

    def __init__(self, segments: Iterable[str]) -> None:
        checked: List[str] = []
        for segment in segments:
            if not isinstance(segment, (Fixed, RedactedText)):
                raise TypeError(
                    "prompt segments must be Fixed or RedactedText, not " + type(segment).__name__
                )
            checked.append(segment)
        self._segments: Tuple[str, ...] = tuple(checked)

    def render(self) -> str:
        return "".join(self._segments)

    def __len__(self) -> int:
        return sum(len(s) for s in self._segments)


class LLMClient(Protocol):
    model: str

    def use_call_log(self, call_log: "object") -> None:
        ...

    def complete_structured(
        self,
        *,
        stage: str,
        submission_id: str,
        attempt: int,
        system: Fixed,
        user: RedactedPrompt,
        schema: Type[T],
    ) -> T:
        """Send one request and return the parsed, schema-valid output.

        `attempt` is the validation-attempt number (1 for the first try). Raises
        `InvalidModelOutput`, `ModelUnavailable`, `SpendLimitReached`, or `ModelAuthError`.
        """
        ...
