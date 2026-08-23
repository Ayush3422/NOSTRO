"""Exception classification — deterministic rules, no model.

Classification is a decision tree over facts we already hold, so a model would
add latency, cost, and nondeterminism while removing auditability. The model's
job starts one step later, at proposing what to do about a classified exception.

`build_exceptions` is exhaustive by construction: every row not consumed by a
match becomes an exception. That is what makes the exception list honest.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from nostro.ingest.loader import QuarantinedRow
from nostro.models import CanonicalRow, Match, ParsedBy, Source
from nostro.normalize.canonical import CanonicalSet


class ExceptionClass(str, Enum):
    MISSING_COUNTERPARTY = "missing_counterparty"
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE_UTR = "duplicate_utr"
    LATE_CREDIT = "late_credit"
    UNPARSEABLE_NARRATION = "unparseable_narration"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    CHARGEBACK_WITHOUT_FORWARD = "chargeback_without_forward"
    QUARANTINED_ROW = "quarantined_row"
    UNKNOWN = "unknown"


class ResolutionKind(str, Enum):
    JOURNAL_ENTRY = "journal_entry"
    CHASE_COUNTERPARTY = "chase_counterparty"
    WRITE_OFF = "write_off"
    MERGE_DUPLICATE = "merge_duplicate"
    NEEDS_HUMAN = "needs_human"


class ExceptionItem(BaseModel):
    exception_id: str
    row_ids: tuple[str, ...]
    exception_class: ExceptionClass
    amount_paise: int
    evidence: str


class ProposedResolution(BaseModel):
    exception_id: str
    kind: ResolutionKind
    rationale: str
    confidence: float
    requires_human: bool = True


def classify_deterministic(
    row: CanonicalRow, cset: CanonicalSet, context: dict
) -> ExceptionItem:
    utr = row.refs.get("utr")
    if utr:
        sharing = [r.row_id for r in cset.bank if r.refs.get("utr") == utr]
        if len(sharing) > 1:
            return ExceptionItem(
                exception_id=f"exc_{row.row_id}", row_ids=(row.row_id,),
                exception_class=ExceptionClass.DUPLICATE_UTR,
                amount_paise=row.amount_paise,
                evidence=f"UTR {utr} appears on {len(sharing)} bank rows: {sorted(sharing)}",
            )

    if row.source is Source.BANK and row.parsed_by is ParsedBy.NONE:
        return ExceptionItem(
            exception_id=f"exc_{row.row_id}", row_ids=(row.row_id,),
            exception_class=ExceptionClass.UNPARSEABLE_NARRATION,
            amount_paise=row.amount_paise,
            evidence=f"no UTR recoverable from narration: {row.narration_raw!r}",
        )

    if row.refs.get("kind") == "chargeback":
        return ExceptionItem(
            exception_id=f"exc_{row.row_id}", row_ids=(row.row_id,),
            exception_class=ExceptionClass.CHARGEBACK_WITHOUT_FORWARD,
            amount_paise=row.amount_paise,
            evidence="chargeback debit with no matching forward entry on the gateway side",
        )

    return ExceptionItem(
        exception_id=f"exc_{row.row_id}", row_ids=(row.row_id,),
        exception_class=ExceptionClass.MISSING_COUNTERPARTY,
        amount_paise=row.amount_paise,
        evidence=(f"{row.source.value} row {row.row_id} for "
                  f"{row.amount_paise} paise on {row.value_date} has no counterparty"),
    )


def build_exceptions(
    cset: CanonicalSet,
    matches: list[Match],
    quarantined: list[QuarantinedRow],
    consumed: set[str],
) -> list[ExceptionItem]:
    touched = set(consumed)
    for match in matches:
        touched.update(match.razorpay_ids)
        touched.update(match.bank_ids)
        touched.update(match.erp_ids)

    items = [
        classify_deterministic(row, cset, {})
        for row in (*cset.razorpay, *cset.bank, *cset.erp)
        if row.row_id not in touched
    ]

    items.extend(
        ExceptionItem(
            exception_id=f"exc_q_{q.source.value}_{q.line_no}",
            row_ids=(f"{q.source.value}:line{q.line_no}",),
            exception_class=ExceptionClass.QUARANTINED_ROW,
            amount_paise=0,
            evidence=f"quarantined at ingest: {q.reason}",
        )
        for q in quarantined
    )

    return sorted(items, key=lambda i: (-i.amount_paise, i.exception_id))
