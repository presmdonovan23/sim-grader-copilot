"""Per-submission record of every model request and response (technical spec section 12.1).

`llm_calls.jsonl` is machine-readable; `llm_calls.md` is the same content for people. Both are
append-only. A "request" event is written BEFORE the call is made, so what was about to be sent is
on disk even if the process dies; a "response" or "error" event is written afterwards. Prompts
contain only redacted text. The API key is never written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

_FENCE = "~~~~~~"


def _fenced(text: str) -> str:
    fence = _FENCE
    while fence in text:
        fence += "~"
    return f"{fence}\n{text}\n{fence}\n"


class CallLog:
    def __init__(self, folder: Path) -> None:
        self.jsonl_path = Path(folder) / "llm_calls.jsonl"
        self.md_path = Path(folder) / "llm_calls.md"

    def _append(self, event: Dict[str, Any], markdown: str) -> None:
        with open(self.jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
        with open(self.md_path, "a", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.flush()

    def log_request(self, event: Dict[str, Any]) -> None:
        req = event["request"]
        sent = "" if event.get("sent", True) else " - NOT SENT (dry run)"
        head = (
            f"## {event['call_id']} | {event['stage']} | {event['submission_id']} | "
            f"attempt {event['attempt']}, network attempt {event['network_attempt']} | "
            f"{event['timestamp']}{sent}\n\n"
        )
        settings = {k: v for k, v in req.items() if k not in ("instructions", "input", "text")}
        fmt = req.get("text", {}).get("format", {})
        settings["response_format"] = f"json_schema {fmt.get('name', '')} (strict={fmt.get('strict')})"
        body = (
            "### Request\n\n"
            f"Settings: `{json.dumps(settings, ensure_ascii=False)}`\n\n"
            "System prompt:\n\n" + _fenced(str(req.get("instructions", ""))) + "\n"
            "Input:\n\n" + _fenced(str(req.get("input", ""))) + "\n"
        )
        self._append(event, head + body)

    def log_response(self, event: Dict[str, Any]) -> None:
        resp = event["response"]
        lines = [
            f"### Response ({event['call_id']})\n\n",
            f"Status: {resp.get('status')} | response id: {resp.get('id')} | "
            f"latency: {event.get('latency_seconds')}s | usage: "
            f"`{json.dumps(event.get('usage'))}` | estimated cost: {event.get('estimated_cost_usd')}\n\n",
            _fenced(str(resp.get("output_text", ""))),
        ]
        if resp.get("validation_error"):
            lines.append(f"\nOutput rejected: {resp['validation_error']}\n")
        self._append(event, "".join(lines) + "\n")

    def log_error(self, event: Dict[str, Any]) -> None:
        text = (
            f"### Error ({event['call_id']})\n\n"
            f"{event['error']} (retryable: {event.get('retryable')}; "
            f"latency: {event.get('latency_seconds')}s)\n\n"
        )
        self._append(event, text)
