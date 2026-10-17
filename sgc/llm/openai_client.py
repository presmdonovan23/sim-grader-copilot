"""OpenAI Responses API client (technical spec section 10.6).

The request dict built by `build_request` is the single source of truth: it is logged, and then
that same dict is passed to the SDK, so the log is exactly what is sent.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Callable, Dict, Optional, Type

from ..config import (
    DEFAULT_REASONING_EFFORT,
    MAX_NETWORK_ATTEMPTS,
    MAX_OUTPUT_TOKENS,
    NETWORK_BACKOFF_BASE_SECONDS,
    NETWORK_BACKOFF_MAX_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)
from ..runlog import get_logger
from .base import Fixed, RedactedPrompt, T
from .calllog import CallLog
from .errors import InvalidModelOutput, ModelAuthError, ModelUnavailable
from .schemas import strict_schema
from .spend import SpendTracker

_AUTH_ERRORS = {"AuthenticationError", "PermissionDeniedError"}
_RETRYABLE_ERRORS = {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_*\-]{4,}")


def resolve_reasoning_effort() -> Optional[str]:
    value = os.environ.get("SGC_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip().lower()
    return None if value in ("", "off", "none") else value


def build_request(
    *,
    model: str,
    stage: str,
    system: Fixed,
    user: RedactedPrompt,
    schema: Type[T],
    reasoning_effort: Optional[str],
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "model": model,
        "instructions": str(system),
        "input": user.render(),
        "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS[stage],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema.__name__,
                "schema": strict_schema(schema),
                "strict": True,
            }
        },
    }
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    return request


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def scrub(text: str) -> str:
    """Remove anything that looks like an API key from text that is about to be logged."""
    return _KEY_PATTERN.sub("sk-[REDACTED]", text)


class _CallCounter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"c{self.n:04d}"


class OpenAIClient:
    def __init__(
        self,
        *,
        model: str,
        spend: SpendTracker,
        reasoning_effort: Optional[str] = None,
        sdk: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.spend = spend
        self.reasoning_effort = reasoning_effort if reasoning_effort is not None else resolve_reasoning_effort()
        self._sdk = sdk
        self._sleep = sleep
        self._clock = clock
        self._call_log: Optional[CallLog] = None
        self._counter = _CallCounter()

    def use_call_log(self, call_log: CallLog) -> None:
        self._call_log = call_log

    def _client(self) -> Any:
        if self._sdk is None:
            import openai

            # max_retries=0: all retries happen here so every attempt is logged.
            self._sdk = openai.OpenAI(max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
        return self._sdk

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
        log = self._call_log
        request = build_request(
            model=self.model,
            stage=stage,
            system=system,
            user=user,
            schema=schema,
            reasoning_effort=self.reasoning_effort,
        )
        chars = len(request["instructions"]) + len(request["input"])

        response: Any = None
        call_id = ""
        started = 0.0
        for network_attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
            self.spend.check(chars)
            call_id = self._counter.next()
            log.log_request(
                {
                    "event": "request",
                    "call_id": call_id,
                    "submission_id": submission_id,
                    "stage": stage,
                    "attempt": attempt,
                    "network_attempt": network_attempt,
                    "timestamp": _now(),
                    "sent": True,
                    "request": request,
                }
            )
            started = self._clock()
            try:
                response = self._client().responses.create(**request)
                break
            except Exception as exc:  # the SDK raises many types; classified below
                latency = round(self._clock() - started, 2)
                name = type(exc).__name__
                status_code = getattr(exc, "status_code", None)
                code = getattr(exc, "code", None)
                no_quota = code == "insufficient_quota"
                retryable = not no_quota and (
                    name in _RETRYABLE_ERRORS
                    or (isinstance(status_code, int) and (status_code in (408, 409, 429) or status_code >= 500))
                )
                message = scrub(f"{name}: {exc}")
                log.log_error(
                    {
                        "event": "error",
                        "call_id": call_id,
                        "submission_id": submission_id,
                        "error": message,
                        "retryable": retryable,
                        "latency_seconds": latency,
                        "timestamp": _now(),
                    }
                )
                get_logger().warning("%s %s call %s failed: %s", submission_id, stage, call_id, message)
                if name in _AUTH_ERRORS or no_quota:
                    raise ModelAuthError(
                        "the OpenAI API rejected the request: "
                        + ("no remaining quota or credit" if no_quota else "check OPENAI_API_KEY and account access")
                    ) from None
                if retryable and network_attempt < MAX_NETWORK_ATTEMPTS:
                    delay = min(NETWORK_BACKOFF_MAX_SECONDS, NETWORK_BACKOFF_BASE_SECONDS * 2 ** (network_attempt - 1))
                    self._sleep(delay + random.uniform(0, 1))
                    continue
                raise ModelUnavailable(f"model call failed ({name})") from None

        return self._handle_response(response, call_id, submission_id, schema, self._clock() - started, log)

    def _handle_response(
        self, response: Any, call_id: str, submission_id: str, schema: Type[T], latency: float, log: CallLog
    ) -> T:
        raw = getattr(response, "output_text", None) or ""
        status = getattr(response, "status", None)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        details = getattr(usage, "output_tokens_details", None)
        reasoning_tokens = getattr(details, "reasoning_tokens", None)
        cost = self.spend.record(submission_id, input_tokens, output_tokens)

        parsed = None
        error: Optional[str] = None
        if status not in (None, "completed"):
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            error = f"response status {status}" + (f" ({reason})" if reason else "")
        elif not raw.strip():
            error = "the model returned no text (possible refusal)"
        else:
            try:
                parsed = schema.model_validate_json(raw)
            except Exception as exc:
                error = "output did not match the schema: " + scrub(str(exc)).splitlines()[0][:300]

        log.log_response(
            {
                "event": "response",
                "call_id": call_id,
                "submission_id": submission_id,
                "timestamp": _now(),
                "response": {
                    "id": getattr(response, "id", None),
                    "status": status,
                    "output_text": raw,
                    "parsed": json.loads(parsed.model_dump_json()) if parsed is not None else None,
                    "validation_error": error,
                },
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_tokens": reasoning_tokens,
                },
                "estimated_cost_usd": None if cost is None else round(cost, 6),
                "latency_seconds": round(latency, 2),
            }
        )
        if parsed is None:
            raise InvalidModelOutput(error or "invalid output", raw)
        return parsed
