"""Dry-run client: builds and logs the first request exactly as it would be sent, then stops."""

from __future__ import annotations

from typing import Optional, Type

from .base import Fixed, RedactedPrompt, T
from .calllog import CallLog
from .openai_client import _CallCounter, _now, build_request, resolve_reasoning_effort


class DryRunComplete(Exception):
    """Raised after the request has been logged (never sent)."""


class DryRunClient:
    def __init__(self, *, model: str) -> None:
        self.model = model
        self._call_log: Optional[CallLog] = None
        self._counter = _CallCounter()

    def use_call_log(self, call_log: CallLog) -> None:
        self._call_log = call_log

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
        if self._call_log is None:
            raise RuntimeError("a call log must be set before sending requests")
        request = build_request(
            model=self.model,
            stage=stage,
            system=system,
            user=user,
            schema=schema,
            reasoning_effort=resolve_reasoning_effort(),
        )
        self._call_log.log_request(
            {
                "event": "request",
                "call_id": self._counter.next(),
                "submission_id": submission_id,
                "stage": stage,
                "attempt": attempt,
                "network_attempt": 1,
                "timestamp": _now(),
                "sent": False,
                "request": request,
            }
        )
        raise DryRunComplete()
