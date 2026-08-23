"""Bank narration parsing.

Design note that belongs in the README: this is the ONLY place in the matching
path where a model is allowed near the data, and it runs second. A UTR is a
structured token in a semi-structured string, which regex handles exactly and
cheaply; the model exists solely for narration the ladder cannot read. Every
parse records which mechanism produced it.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel

from nostro.models import ParsedBy

_UTR = re.compile(r"\b(UTR[0-9A-Z]{6,20})\b", re.IGNORECASE)
_RRN = re.compile(r"\b(RRN[0-9]{4,16})\b", re.IGNORECASE)
_LOOSE_UTR = re.compile(r"\b([0-9]{12,22})\b")

_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chargeback", re.compile(r"charge\s*back", re.IGNORECASE)),
    ("refund", re.compile(r"refund|rfnd", re.IGNORECASE)),
    ("settlement", re.compile(r"settle|rzpy|razorpay", re.IGNORECASE)),
)


class ParsedNarration(BaseModel):
    utr: str | None = None
    rrn: str | None = None
    kind: str = "unknown"
    parsed_by: ParsedBy = ParsedBy.NONE
    confidence: float = 0.0


def _classify(text: str) -> str:
    for kind, pattern in _KINDS:
        if pattern.search(text):
            return kind
    return "unknown"


class NarrationParser:
    def __init__(
        self,
        llm_fallback: Callable[[str], ParsedNarration | None] | None = None,
    ) -> None:
        self._llm = llm_fallback
        self.stats: dict[str, int] = {"regex_hits": 0, "llm_calls": 0, "misses": 0}

    def parse(self, narration: str) -> ParsedNarration:
        text = narration or ""

        utr_match = _UTR.search(text)
        if utr_match is None:
            # Corrupted narration often collapses spacing; retry on a squeezed copy.
            squeezed = re.sub(r"[^0-9A-Za-z]", "", text)
            utr_match = _UTR.search(squeezed)

        if utr_match:
            self.stats["regex_hits"] += 1
            rrn_match = _RRN.search(text)
            return ParsedNarration(
                utr=utr_match.group(1).upper(),
                rrn=rrn_match.group(1).upper() if rrn_match else None,
                kind=_classify(text), parsed_by=ParsedBy.REGEX, confidence=0.99,
            )

        loose = _LOOSE_UTR.search(text)
        if loose:
            self.stats["regex_hits"] += 1
            return ParsedNarration(utr=loose.group(1), kind=_classify(text),
                                   parsed_by=ParsedBy.REGEX, confidence=0.70)

        if self._llm is not None:
            self.stats["llm_calls"] += 1
            try:
                result = self._llm(text)
            except Exception:
                # A model outage must degrade the close, never stop it.
                result = None
            if result is not None:
                self.stats["regex_hits"] += 0
                return result.model_copy(update={"parsed_by": ParsedBy.LLM})

        self.stats["misses"] += 1
        return ParsedNarration(kind=_classify(text), parsed_by=ParsedBy.NONE,
                               confidence=0.0)


def parser_stats(parser: NarrationParser) -> dict[str, int]:
    return dict(parser.stats)
