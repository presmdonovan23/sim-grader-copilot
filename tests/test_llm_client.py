from __future__ import annotations

import json

import pytest
from fakes import APIConnectionError, AuthenticationError, FakeAPIError, FakeResponse, FakeSDK

from sgc.llm.base import Fixed, RedactedPrompt
from sgc.llm.calllog import CallLog
from sgc.llm.dryrun import DryRunClient, DryRunComplete
from sgc.llm.errors import InvalidModelOutput, ModelAuthError, ModelUnavailable, SpendLimitReached
from sgc.llm.openai_client import OpenAIClient, build_request, scrub
from sgc.llm.schemas import ScreeningResult, strict_schema
from sgc.llm.spend import SpendTracker
from sgc.redact import RedactedText, Redactor

GOOD = '{"flags": []}'


def make_client(tmp_path, outcomes, *, ceiling=None, sleeps=None, model="gpt-6.1-sol"):
    sdk = FakeSDK(outcomes)
    spend = SpendTracker(model, ceiling)
    slept = sleeps if sleeps is not None else []
    client = OpenAIClient(model=model, spend=spend, reasoning_effort="medium", sdk=sdk, sleep=slept.append)
    client.use_call_log(CallLog(tmp_path))
    return client, sdk, spend


def redacted(text):
    return Redactor(["Zzyzx Qwerty"]).redact(text).text


def call(client, text="Hello there."):
    return client.complete_structured(
        stage="screening",
        submission_id="S001",
        attempt=1,
        system=Fixed("SYSTEM"),
        user=RedactedPrompt([Fixed("Text: "), redacted(text)]),
        schema=ScreeningResult,
    )


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]


def test_prompt_rejects_plain_strings():
    with pytest.raises(TypeError):
        RedactedPrompt([Fixed("ok"), "raw student text"])
    with pytest.raises(TypeError):
        RedactedText("raw")


def test_strict_schema_shape():
    schema = strict_schema(ScreeningResult)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert "default" not in json.dumps(schema)
    assert schema["$defs"]["FlagType"]["enum"][0] == "ai_artifact"


def test_logged_request_is_exactly_what_the_sdk_received(tmp_path):
    client, sdk, _ = make_client(tmp_path, [FakeResponse(GOOD)])
    assert call(client).flags == []
    logged = [e for e in events(tmp_path) if e["event"] == "request"]
    assert len(logged) == 1 and logged[0]["sent"] is True
    assert logged[0]["request"] == sdk.requests[0]
    request = sdk.requests[0]
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "medium"}
    assert request["text"]["format"]["strict"] is True
    assert "tools" not in request
    assert request["instructions"] == "SYSTEM" and request["input"] == "Text: Hello there."


def test_request_is_logged_before_the_call_is_made(tmp_path):
    seen = []
    client, sdk, _ = make_client(tmp_path, [FakeResponse(GOOD)])
    original = sdk.responses.create

    def spy(**kwargs):
        seen.append([e["event"] for e in events(tmp_path)])
        return original(**kwargs)

    sdk.responses.create = spy
    call(client)
    assert seen == [["request"]]


def test_response_logged_with_usage_and_cost(tmp_path):
    client, _, spend = make_client(tmp_path, [FakeResponse(GOOD, input_tokens=1_000_000, output_tokens=100_000)])
    call(client)
    response = [e for e in events(tmp_path) if e["event"] == "response"][0]
    assert response["response"]["output_text"] == GOOD
    assert response["response"]["parsed"] == {"flags": []}
    assert response["usage"]["input_tokens"] == 1_000_000
    assert response["estimated_cost_usd"] == pytest.approx(2.0 + 1.0)
    assert spend.spent == pytest.approx(3.0)
    md = (tmp_path / "llm_calls.md").read_text()
    assert "System prompt" in md and "Hello there." in md


def test_network_errors_are_retried_with_backoff_and_each_attempt_logged(tmp_path):
    sleeps = []
    client, sdk, _ = make_client(
        tmp_path,
        [APIConnectionError("boom"), FakeAPIError("busy", status_code=429), FakeResponse(GOOD)],
        sleeps=sleeps,
    )
    call(client)
    assert len(sdk.requests) == 3
    kinds = [e["event"] for e in events(tmp_path)]
    assert kinds == ["request", "error", "request", "error", "request", "response"]
    assert [e["network_attempt"] for e in events(tmp_path) if e["event"] == "request"] == [1, 2, 3]
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1] + 1


def test_gives_up_after_five_attempts(tmp_path):
    client, sdk, _ = make_client(tmp_path, [APIConnectionError("down")] * 5)
    with pytest.raises(ModelUnavailable):
        call(client)
    assert len(sdk.requests) == 5


def test_non_retryable_error_is_not_retried(tmp_path):
    client, sdk, _ = make_client(tmp_path, [FakeAPIError("bad request", status_code=400)])
    with pytest.raises(ModelUnavailable):
        call(client)
    assert len(sdk.requests) == 1


def test_auth_and_quota_errors_are_fatal(tmp_path):
    client, _, _ = make_client(tmp_path, [AuthenticationError("Incorrect API key provided: sk-abcd1234efgh", status_code=401)])
    with pytest.raises(ModelAuthError):
        call(client)
    client, _, _ = make_client(tmp_path, [FakeAPIError("quota", status_code=429, code="insufficient_quota")])
    with pytest.raises(ModelAuthError):
        call(client)


def test_api_key_never_reaches_the_logs(tmp_path):
    secret = "sk-abcd1234efgh5678"
    client, _, _ = make_client(tmp_path, [AuthenticationError(f"Incorrect API key provided: {secret}", status_code=401)])
    with pytest.raises(ModelAuthError):
        call(client)
    for name in ("llm_calls.jsonl", "llm_calls.md"):
        assert secret not in (tmp_path / name).read_text()
    assert scrub("key sk-proj-AbC_123-xyz here") == "key sk-[REDACTED] here"


def test_invalid_output_is_logged_and_raised(tmp_path):
    client, _, _ = make_client(tmp_path, [FakeResponse("not json"), FakeResponse(""), FakeResponse(GOOD, status="incomplete")])
    for _ in range(3):
        with pytest.raises(InvalidModelOutput):
            call(client)
    responses = [e for e in events(tmp_path) if e["event"] == "response"]
    assert len(responses) == 3
    assert all(r["response"]["validation_error"] for r in responses)
    assert responses[0]["response"]["output_text"] == "not json"
    assert "max_output_tokens" in responses[2]["response"]["validation_error"]


def test_spend_limit_stops_before_the_call(tmp_path):
    client, sdk, spend = make_client(tmp_path, [FakeResponse(GOOD, input_tokens=1_000_000, output_tokens=0)] * 2, ceiling=1.0)
    call(client)                       # costs $2.00, over the ceiling
    with pytest.raises(SpendLimitReached):
        call(client)
    assert len(sdk.requests) == 1
    assert spend.exhausted


def test_unknown_model_cost_is_none(tmp_path):
    client, _, spend = make_client(tmp_path, [FakeResponse(GOOD)], model="mystery-model")
    call(client)
    assert spend.summary()["estimated_cost_usd"] is None and not spend.summary()["price_known"]


def test_a_call_log_is_required(tmp_path):
    client = OpenAIClient(model="gpt-6.1-sol", spend=SpendTracker("gpt-6.1-sol"), sdk=FakeSDK([FakeResponse(GOOD)]))
    with pytest.raises(RuntimeError):
        call(client)


def test_dry_run_client_logs_unsent_request(tmp_path):
    client = DryRunClient(model="gpt-6.1-sol")
    client.use_call_log(CallLog(tmp_path))
    with pytest.raises(DryRunComplete):
        call(client)
    (event,) = events(tmp_path)
    assert event["sent"] is False
    assert event["request"]["store"] is False
    assert "NOT SENT" in (tmp_path / "llm_calls.md").read_text()


def test_build_request_omits_reasoning_when_off():
    request = build_request(
        model="m", stage="grading", system=Fixed("s"), user=RedactedPrompt([Fixed("u")]),
        schema=ScreeningResult, reasoning_effort=None,
    )
    assert "reasoning" not in request
