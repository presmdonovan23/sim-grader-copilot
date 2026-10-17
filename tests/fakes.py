"""Test doubles for the model stage."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from sgc.llm.schemas import (
    CategoryScore,
    Flag,
    FlagType,
    GradingResult,
    ScreeningResult,
    WeakestAreaResult,
)


class ScriptedLLM:
    """An LLMClient that returns scripted results per stage and records every call.

    A script entry is a result object, an exception to raise, or a callable taking the rendered
    prompt and returning a result.
    """

    model = "fake-model"

    def __init__(self, script: Dict[str, List[Any]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: List[Dict[str, Any]] = []

    def use_call_log(self, call_log: object) -> None:
        pass

    def complete_structured(self, *, stage, submission_id, attempt, system, user, schema):
        prompt = user.render()
        self.calls.append(
            {"stage": stage, "attempt": attempt, "prompt": prompt, "system": str(system), "sid": submission_id}
        )
        queue = self.script.get(stage)
        if not queue:
            raise AssertionError(f"unexpected {stage} call")
        item = queue.pop(0)
        if callable(item):
            item = item(prompt)
        if isinstance(item, BaseException):
            raise item
        return item

    def stage_calls(self, stage: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["stage"] == stage]


def score(category: str, points: int, quotes: Optional[List[str]] = None, *, borderline=False,
          alternative=None, reason=None, rationale="Because.") -> CategoryScore:
    return CategoryScore(
        category=category,
        points=points,
        rationale=rationale,
        evidence_quotes=quotes if quotes is not None else ([] if points == 0 else ["PLACEHOLDER"]),
        borderline=borderline,
        alternative_points=alternative,
        borderline_reason=reason,
    )


def grading(rubric, points: List[int], quote: str, **overrides: Any) -> GradingResult:
    scores = []
    for category, pts in zip(rubric.categories, points):
        extra = overrides.get(category.name, {})
        scores.append(score(category.name, pts, [quote] if pts else [], **extra))
    return GradingResult(scores=scores)


def screening(*flags: Flag) -> ScreeningResult:
    return ScreeningResult(flags=list(flags))


def flag(type_: FlagType, quote: str, explanation: str = "Noted.") -> Flag:
    return Flag(type=type_, quote=quote, explanation=explanation)


def weakest(category: str, quote: str) -> WeakestAreaResult:
    return WeakestAreaResult(category=category, explanation="Least extended.", suggestion="Go further.", quote=quote)


# --- fake OpenAI SDK ---------------------------------------------------------------------


class FakeResponse:
    def __init__(self, text: str, *, status: str = "completed", input_tokens: int = 1000,
                 output_tokens: int = 200, response_id: str = "resp_1") -> None:
        self.output_text = text
        self.status = status
        self.id = response_id
        self.incomplete_details = SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None
        self.usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            output_tokens_details=SimpleNamespace(reasoning_tokens=50),
        )


class FakeSDK:
    """Stands in for `openai.OpenAI`: `sdk.responses.create(**kwargs)` pops scripted outcomes."""

    def __init__(self, outcomes: List[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: List[Dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(json.loads(json.dumps(kwargs)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class APIConnectionError(FakeAPIError):
    """Named like the SDK's class so the client classifies it as retryable."""


class AuthenticationError(FakeAPIError):
    pass
