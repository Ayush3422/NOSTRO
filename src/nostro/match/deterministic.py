"""Deterministic 1:1 matching.

Three passes, strongest evidence first, each consuming rows the later passes
may no longer touch:

  1. reference identity  — same order_id across Razorpay and ERP (MatchMethod.REFERENCE;
                            the residual here is fee+GST, not amount-match evidence,
                            since Razorpay canonicalises on net and ERP on gross)
  2. exact               — same amount, within the date window
  3. tolerance            — amount within tolerance, within the date window

The pass ordering is the whole design. A greedy single pass would let a
one-paise-off pairing consume a row that an exact pairing needed, and every
downstream metric would inherit the error.
"""
from __future__ import annotations

from pydantic import BaseModel

from nostro.match.blocking import Blocks, BlockingConfig, candidates_for
from nostro.models import Match, MatchMethod, Source
from nostro.normalize.canonical import CanonicalSet


class DeterministicResult(BaseModel):
    matches: list[Match] = []
    consumed: set[str] = set()


def _pair_id(left: str, right: str) -> str:
    return f"m_{left}__{right}"


def match_deterministic(
    cset: CanonicalSet, blocks: Blocks, cfg: BlockingConfig
) -> DeterministicResult:
    result = DeterministicResult()
    index = blocks.row_index

    def emit(left_id: str, right_id: str, method: MatchMethod, residual: int) -> None:
        left, right = index[left_id], index[right_id]
        buckets: dict[Source, list[str]] = {Source.RAZORPAY: [], Source.BANK: [],
                                            Source.ERP: []}
        buckets[left.source].append(left_id)
        buckets[right.source].append(right_id)
        result.matches.append(Match(
            match_id=_pair_id(left_id, right_id),
            razorpay_ids=tuple(buckets[Source.RAZORPAY]),
            bank_ids=tuple(buckets[Source.BANK]),
            erp_ids=tuple(buckets[Source.ERP]),
            score=1.0 if method is MatchMethod.EXACT else 0.8,
            method=method, residual_paise=residual,
        ))
        result.consumed.update((left_id, right_id))

    ordered = sorted((*cset.razorpay, *cset.bank, *cset.erp), key=lambda r: r.row_id)

    # Pass 1 — reference identity.
    for row in ordered:
        if row.row_id in result.consumed or row.source is not Source.RAZORPAY:
            continue
        order = row.refs.get("order_id")
        if not order:
            continue
        for rid in sorted(blocks.by_order.get(order, ())):
            other = index[rid]
            if (rid in result.consumed or other.source is not Source.ERP
                    or other.direction is not row.direction):
                continue
            emit(row.row_id, rid, MatchMethod.REFERENCE,
                 abs(row.amount_paise - other.amount_paise))
            break

    # Pass 2 — exact amount. Pass 3 — tolerance. Same loop, different predicate.
    for method, max_residual in (
        (MatchMethod.EXACT, 0),
        (MatchMethod.TOLERANCE, cfg.amount_tolerance_paise),
    ):
        for row in ordered:
            if row.row_id in result.consumed or row.source is Source.ERP:
                continue
            best: tuple[int, str] | None = None
            for rid in sorted(candidates_for(row, blocks, cfg)):
                if rid in result.consumed:
                    continue
                other = index[rid]
                if other.source is Source.ERP:
                    continue
                residual = abs(row.amount_paise - other.amount_paise)
                if residual > max_residual:
                    continue
                if best is None or residual < best[0]:
                    best = (residual, rid)
            if best is not None:
                emit(row.row_id, best[1], method, best[0])

    return result
