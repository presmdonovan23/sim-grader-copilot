"""De-identification (technical spec section 8).

Student names from a user-supplied list are removed by code, never by a model, before any text
reaches the language model. After removal an independent, stricter check looks for surviving
names and fails closed: the submission becomes "Not graded" instead of being sent.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set, Tuple

from .config import MIN_STANDALONE_NAME_TOKEN_LEN, NAME_PLACEHOLDER
from .errors import NamesFileError, NotGraded

HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "mx", "miss", "prof", "professor"})

_ZERO_WIDTH_AND_BIDI = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x200E, 0x200F]
    + list(range(0x202A, 0x202F))
    + list(range(0x2066, 0x206A))
)
_QUOTE_CHARS = "\u2019\u2018\u02bc`\u00b4\u2032"
_DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"

# A word broken across lines with a hyphen: "Sus-\nan" -> "Susan" (next line starts lowercase).
_LINE_BREAK_HYPHEN = re.compile(r"(?<=[^\W\d_])[-\u2010][ \t]*\r?\n[ \t]*(?=[a-z\u00e0-\u00ff])")

_SEP = r"\s+"
_COMMA = r"\s*,\s*"
_INITIAL_SEP = r"(?:\.\s*|\s+)"
_HYPHEN = r"(?:\s*-\s*|\s+)"


class RedactionVerificationError(NotGraded):
    """A listed name was still present after redaction."""


# --- type guard ---------------------------------------------------------------------------

_TOKEN = object()


class RedactedText(str):
    """Text that has been de-identified and verified.

    Instances can only be created by this module (`Redactor.redact`). Everything that is sent to
    a model is assembled from RedactedText, so un-redacted text cannot reach the model client.
    """

    def __new__(cls, value: str, _token: object = None) -> "RedactedText":
        if _token is not _TOKEN:
            raise TypeError("RedactedText can only be created by the redaction step")
        return super().__new__(cls, value)


@dataclass
class RedactionResult:
    text: RedactedText
    replacements: int


# --- names file ---------------------------------------------------------------------------


def parse_names(path: Path) -> List[str]:
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise NamesFileError(f"Cannot read names file {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise NamesFileError(f"Names file {path} is not valid UTF-8") from exc
    return parse_names_text(raw, source=str(path))


def parse_names_text(raw: str, source: str = "names") -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()
    for line in raw.lstrip("\ufeff").splitlines():
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        key = fold_text(line)
        if key in seen:
            continue
        seen.add(key)
        names.append(line)
    if not names:
        raise NamesFileError(f"Names file {source} contains no names")
    return names


# --- normalization ------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Normalize the text before matching and before it is sent anywhere."""
    text = text.translate(_ZERO_WIDTH_AND_BIDI)
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    text = "".join(
        " " if (ch != " " and unicodedata.category(ch) == "Zs") else ch for ch in text
    )
    text = unicodedata.normalize("NFC", text)
    return _LINE_BREAK_HYPHEN.sub("", text)


def _fold_char(ch: str) -> str:
    if ch in _QUOTE_CHARS:
        return "'"
    if ch in _DASH_CHARS:
        return "-"
    base = unicodedata.normalize("NFD", ch)[0]
    lowered = base.lower()
    return lowered if len(lowered) == 1 else base


def fold_text(text: str) -> str:
    """Case-fold and strip accents, one output character per input character."""
    return "".join(_fold_char(ch) for ch in text)


# --- variants -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    plain: str      # approximate text matched; used to try longer variants first
    pattern: str


def _tok(token: str) -> str:
    out = []
    for ch in token:
        if ch == "-":
            out.append(_HYPHEN)
        elif ch == "'":
            out.append("'?")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _clean_token(token: str) -> str:
    return fold_text(token).strip(".,;:()[]\"")


def name_core_tokens(name: str) -> Tuple[List[str], List[str]]:
    """Return (honorifics, core tokens) for a name, folded."""
    tokens = [t for t in (_clean_token(t) for t in name.split()) if t]
    honorifics: List[str] = []
    while len(tokens) > 1 and tokens[0].rstrip(".") in HONORIFICS:
        honorifics.append(tokens.pop(0))
    return honorifics, tokens


def make_variants(name: str) -> List[Variant]:
    honorifics, core = name_core_tokens(name)
    if not core:
        return []
    variants: List[Variant] = []

    def add(plain: str, pattern: str) -> None:
        variants.append(Variant(plain=plain, pattern=pattern))

    def add_standalone() -> None:
        for token in core:
            if len(token) >= MIN_STANDALONE_NAME_TOKEN_LEN:
                add(token, _tok(token))
            if "-" in token:
                for part in token.split("-"):
                    if len(part) >= MIN_STANDALONE_NAME_TOKEN_LEN:
                        add(part, _tok(part))

    full = _SEP.join(_tok(t) for t in core)
    add(" ".join(core), full)
    if honorifics:
        hon = _SEP.join(re.escape(h.rstrip(".")) + r"\.?" for h in honorifics)
        add(" ".join(honorifics + core), hon + _SEP + full)

    if len(core) == 1:
        add_standalone()
        return variants

    first, last = core[0], core[-1]
    rest = core[:-1]                       # first name and any middle names
    add(f"{last}, {' '.join(rest)}", _tok(last) + _COMMA + _SEP.join(_tok(t) for t in rest))
    add(f"{last} {' '.join(rest)}", _tok(last) + _SEP + _SEP.join(_tok(t) for t in rest))

    if any(len(t) < MIN_STANDALONE_NAME_TOKEN_LEN for t in core):
        # Short tokens (e.g. "Li") are never matched alone or as initials; only the full-name
        # forms above are used, plus standalone forms of the longer tokens.
        add_standalone()
        return variants

    if len(core) > 2:
        add(f"{first} {last}", _tok(first) + _SEP + _tok(last))
        add(f"{last}, {first}", _tok(last) + _COMMA + _tok(first))
        add(f"{first[0]}. {last}", re.escape(first[0]) + _INITIAL_SEP + _tok(last))
    initials_pat = _INITIAL_SEP.join(re.escape(t[0]) for t in rest)
    add(f"{first[0]}. {last}", initials_pat + _INITIAL_SEP + _tok(last))
    add(f"{first} {last[0]}.", _SEP.join(_tok(t) for t in rest) + _SEP + re.escape(last[0]) + r"\.?")
    add(f"{last}, {first[0]}.", _tok(last) + _COMMA + re.escape(first[0]) + r"\.?")
    add_standalone()

    seen: Set[str] = set()
    unique = []
    for v in variants:
        if v.pattern not in seen:
            seen.add(v.pattern)
            unique.append(v)
    return unique


def build_matcher(names: Iterable[str]) -> "re.Pattern[str]":
    variants: List[Variant] = []
    for name in names:
        variants.extend(make_variants(name))
    # Longer variants first so "Susan Lee" is replaced as one unit.
    variants.sort(key=lambda v: -len(v.plain))
    seen: Set[str] = set()
    parts = []
    for v in variants:
        if v.pattern not in seen:
            seen.add(v.pattern)
            parts.append(f"(?:{v.pattern})")
    body = "|".join(parts)
    if not body:
        raise NamesFileError("The names list produced nothing to match")
    # Optional possessive after the name is included in the replaced span.
    return re.compile(rf"(?<!\w)(?:{body})(?:'s|s')?(?!\w)")


# --- verification -------------------------------------------------------------------------


def _aggressive_tokens(text: str) -> List[str]:
    """Fold, strip accents, and split on every non-alphanumeric character."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    chars = []
    for ch in decomposed:
        if unicodedata.combining(ch):
            continue
        chars.append(ch if ch.isalnum() else " ")
    return "".join(chars).split()


class Verifier:
    """Independent, stricter check that no listed name survives redaction."""

    def __init__(self, names: Iterable[str]) -> None:
        self._tokens: Set[str] = set()
        self._targets: Set[str] = set()
        for name in names:
            _, core = name_core_tokens(name)
            name_tokens: List[str] = []
            for token in core:
                name_tokens.extend(_aggressive_tokens(token))
            for t in name_tokens:
                if len(t) >= MIN_STANDALONE_NAME_TOKEN_LEN:
                    self._tokens.add(t)
            for ordered in (name_tokens, name_tokens[-1:] + name_tokens[:-1]):
                target = "".join(ordered)
                if len(target) >= 4:
                    self._targets.add(target)
        self._max_target = max((len(t) for t in self._targets), default=0)

    def survivors(self, text: str) -> bool:
        tokens = _aggressive_tokens(text)
        token_set = set(tokens)
        if token_set & self._tokens:
            return True
        # Spaced-out or run-together names: any run of whole tokens that spells a full name.
        for i in range(len(tokens)):
            acc = ""
            for j in range(i, len(tokens)):
                acc += tokens[j]
                if len(acc) > self._max_target:
                    break
                if acc in self._targets:
                    return True
        return False


# --- redaction ----------------------------------------------------------------------------


class Redactor:
    def __init__(self, names: Iterable[str]) -> None:
        names = list(names)
        if not names:
            raise NamesFileError("No names to redact")
        self._matcher = build_matcher(names)
        self._verifier = Verifier(names)

    def redact(self, text: str) -> RedactionResult:
        cleaned = clean_text(text)
        folded = fold_text(cleaned)
        pieces: List[str] = []
        last = 0
        count = 0
        for match in self._matcher.finditer(folded):
            if match.start() == match.end():
                continue
            pieces.append(cleaned[last:match.start()])
            pieces.append(NAME_PLACEHOLDER)
            last = match.end()
            count += 1
        pieces.append(cleaned[last:])
        redacted = "".join(pieces)
        return RedactionResult(text=finalize(redacted, self._verifier), replacements=count)


def finalize(redacted: str, verifier: Verifier) -> RedactedText:
    """The only way to make a RedactedText: the verification must pass."""
    if verifier.survivors(redacted):
        raise RedactionVerificationError("a listed name remained after redaction")
    return RedactedText(redacted, _TOKEN)


# --- derived text -------------------------------------------------------------------------
# Pieces of already-verified text, or verified text with fixed markers swapped in, cannot contain
# a listed name, so these helpers can produce RedactedText without re-running verification.


def truncate(text: RedactedText, limit: int) -> RedactedText:
    """Cut at a whitespace boundary so that no word is split into a new fragment."""
    if len(text) <= limit:
        return text
    cut = str(text)[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    return RedactedText(cut[:boundary] if boundary > 0 else "", _TOKEN)


def replace_spans(text: RedactedText, spans: Iterable[Tuple[int, int]], marker: str) -> RedactedText:
    """Replace each (start, end) span of `text` with the fixed `marker` text.

    Spans are widened to whole words first, so a partial word is never left behind.
    """
    plain = str(text)
    widened: List[Tuple[int, int]] = []
    for start, end in spans:
        while start > 0 and not plain[start - 1].isspace():
            start -= 1
        while end < len(plain) and not plain[end].isspace():
            end += 1
        widened.append((start, end))
    merged: List[List[int]] = []
    for start, end in sorted(widened):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces: List[str] = []
    last = 0
    for start, end in merged:
        pieces.append(plain[last:start])
        pieces.append(marker)
        last = end
    pieces.append(plain[last:])
    return RedactedText("".join(pieces), _TOKEN)
