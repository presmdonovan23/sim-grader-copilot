"""Token, cost, and --max-spend tracking (technical spec section 10.6)."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..config import MODEL_PRICES_PER_MILLION
from .errors import SpendLimitReached


def price_for(model: str) -> Optional[Tuple[float, float]]:
    return MODEL_PRICES_PER_MILLION.get(model)


class SpendTracker:
    def __init__(self, model: str, ceiling_usd: Optional[float] = None) -> None:
        self.model = model
        self.ceiling = ceiling_usd
        self.prices = price_for(model)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.spent = 0.0
        self.by_submission: Dict[str, float] = {}
        self.exhausted = False

    def estimate_input_cost(self, chars: int) -> float:
        if self.prices is None:
            return 0.0
        return (chars / 4) * self.prices[0] / 1_000_000

    def check(self, chars: int) -> None:
        """Raise before a call that would push spending past the ceiling."""
        if self.ceiling is None:
            return
        if self.spent + self.estimate_input_cost(chars) > self.ceiling:
            self.exhausted = True
            raise SpendLimitReached(
                f"spend limit reached (${self.spent:.2f} spent of ${self.ceiling:.2f})"
            )

    def record(self, submission_id: str, input_tokens: int, output_tokens: int) -> Optional[float]:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.prices is None:
            return None
        cost = (input_tokens * self.prices[0] + output_tokens * self.prices[1]) / 1_000_000
        self.spent += cost
        self.by_submission[submission_id] = self.by_submission.get(submission_id, 0.0) + cost
        return cost

    def summary(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.spent, 4) if self.prices else None,
            "price_known": self.prices is not None,
        }
