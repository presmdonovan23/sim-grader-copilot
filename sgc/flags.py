"""Flag and hidden-text records shared by the screening and grading stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .redact import RedactedText


@dataclass
class HiddenText:
    location: str
    text: RedactedText  # full hidden text, de-identified; truncated only when written to disk

    @property
    def deliberate(self) -> bool:
        """White, tiny, off-page, or `vanish` text is raised as a flag by itself.

        Comments and deleted tracked changes are ordinary editing leftovers; they are still shown
        to the screening step but do not raise a flag on their own.
        """
        return self.location != "comment" and not self.location.endswith("(deleted tracked change)")


@dataclass
class FlagRecord:
    type: str                              # FlagType value
    quote: str
    explanation: str
    source: str                            # "hidden_text", "pattern", "wordlist", "screening"
    located: bool = True                   # the quote was found in the text it should come from
    in_hidden: bool = False                # the quote comes from hidden text, not the visible text
    span: Optional[Tuple[int, int]] = None  # position in the visible (redacted) text, if there

    def to_json(self) -> dict:
        return {
            "type": self.type,
            "quote": self.quote,
            "explanation": self.explanation,
            "source": self.source,
            "quote_located": self.located,
            "in_hidden_text": self.in_hidden,
        }
