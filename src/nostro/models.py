"""Shared vocabulary. Every stage of the pipeline speaks in these types."""
from __future__ import annotations

from datetime import date
from enum import Enum
from itertools import combinations, product

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Source(str, Enum):
    RAZORPAY = "razorpay"
    BANK = "bank"
    ERP = "erp"


class Direction(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class ParsedBy(str, Enum):
    REGEX = "regex"
    LLM = "llm"
    NONE = "none"


class MatchMethod(str, Enum):
    REFERENCE = "reference"
    EXACT = "exact"
    TOLERANCE = "tolerance"
    SUBSET_SUM = "subset_sum"
    ASSIGNMENT = "assignment"


class CanonicalRow(BaseModel):
    """One normalised row from any source. amount_paise is StrictInt so that a
    float slipping in from a CSV parser is a hard error, not a silent coercion."""

    model_config = ConfigDict(frozen=True)

    source: Source
    row_id: str
    amount_paise: StrictInt
    direction: Direction
    value_date: date
    settlement_cycle: str | None = None
    refs: dict[str, str] = Field(default_factory=dict)
    narration_raw: str | None = None
    parsed_by: ParsedBy = ParsedBy.NONE


def _cross_source_pairs(
    razorpay_ids: tuple[str, ...],
    bank_ids: tuple[str, ...],
    erp_ids: tuple[str, ...],
) -> set[tuple[str, str]]:
    groups = [razorpay_ids, bank_ids, erp_ids]
    pairs: set[tuple[str, str]] = set()
    for left, right in combinations(groups, 2):
        for a, b in product(left, right):
            pairs.add((a, b) if a < b else (b, a))
    return pairs


class Match(BaseModel):
    """A reconciliation assertion produced by the matcher."""

    model_config = ConfigDict(frozen=True)

    match_id: str
    razorpay_ids: tuple[str, ...] = ()
    bank_ids: tuple[str, ...] = ()
    erp_ids: tuple[str, ...] = ()
    score: float
    probability: float = 0.0
    method: MatchMethod
    residual_paise: StrictInt = 0

    def pairs(self) -> set[tuple[str, str]]:
        return _cross_source_pairs(self.razorpay_ids, self.bank_ids, self.erp_ids)


class GroundTruthLink(BaseModel):
    """What the generator actually linked. The only source of labels."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    razorpay_ids: tuple[str, ...] = ()
    bank_ids: tuple[str, ...] = ()
    erp_ids: tuple[str, ...] = ()

    def pairs(self) -> set[tuple[str, str]]:
        return _cross_source_pairs(self.razorpay_ids, self.bank_ids, self.erp_ids)
