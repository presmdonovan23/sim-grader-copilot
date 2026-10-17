"""Structured-output schemas for the three model calls (technical spec section 10)."""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional, Type

from pydantic import BaseModel


class FlagType(str, Enum):
    AI_ARTIFACT = "ai_artifact"
    FOURTH_WALL = "fourth_wall"
    PROFANITY = "profanity"
    UNRELATED_TEXT = "unrelated_text"
    PROMPT_INJECTION = "prompt_injection"
    PATIENT_IDENTIFIER = "patient_identifier"
    REAL_PATIENT_CARE = "real_patient_care"


class Flag(BaseModel):
    type: FlagType
    quote: str
    explanation: str


class ScreeningResult(BaseModel):
    flags: List[Flag]


class CategoryScore(BaseModel):
    category: str
    points: int
    rationale: str
    evidence_quotes: List[str]
    borderline: bool
    alternative_points: Optional[int]
    borderline_reason: Optional[str]


class GradingResult(BaseModel):
    scores: List[CategoryScore]


class WeakestAreaResult(BaseModel):
    category: str
    explanation: str
    suggestion: str
    quote: str


def strict_schema(model: Type[BaseModel]) -> dict:
    """JSON schema in the form the structured-output API requires.

    Every object has `additionalProperties: false` and lists all its properties as required;
    titles and defaults are dropped.
    """

    def fix(node: Any) -> Any:
        if isinstance(node, list):
            return [fix(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in ("title", "default"):
                continue
            if key in ("properties", "$defs"):
                out[key] = {name: fix(sub) for name, sub in value.items()}
            else:
                out[key] = fix(value)
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return fix(model.model_json_schema())
