"""Evidence features and a monotone raw score.

Deliberately a hand-written scoring function rather than a learned model. With
one merchant's data and a solver that already encodes most of the structure, a
learned matcher would mostly memorise the generator. The learning happens in
calibration instead, where it is cheap, inspectable, and does not decide matches.
"""
from __future__ import annotations

from pydantic import BaseModel

from nostro.match.blocking import Blocks
from nostro.models import Match, MatchMethod, ParsedBy

_METHOD_RANK = {
    MatchMethod.EXACT: 3,
    MatchMethod.REFERENCE: 3,  # a shared order_id is direct foreign-key evidence,
    # as strong as an exact amount agreement.
    MatchMethod.SUBSET_SUM: 2,
    MatchMethod.ASSIGNMENT: 1,
    MatchMethod.TOLERANCE: 1,
}


class MatchFeatures(BaseModel):
    residual_paise: int
    date_gap_days: int
    subset_size: int
    has_ref_link: bool
    method_rank: int
    narration_parsed: bool


def extract_features(match: Match, blocks: Blocks) -> MatchFeatures:
    ids = (*match.razorpay_ids, *match.bank_ids, *match.erp_ids)
    rows = [blocks.row_index[rid] for rid in ids if rid in blocks.row_index]

    dates = [r.value_date for r in rows]
    gap = (max(dates) - min(dates)).days if dates else 0

    orders = [r.refs.get("order_id") for r in rows if r.refs.get("order_id")]
    utrs = [r.refs.get("utr") for r in rows if r.refs.get("utr")]
    has_ref = len(set(orders)) == 1 and len(orders) > 1 or len(set(utrs)) == 1 and len(utrs) > 1

    parsed = any(r.parsed_by is not ParsedBy.NONE for r in rows if r.narration_raw)

    return MatchFeatures(
        residual_paise=match.residual_paise,
        date_gap_days=gap,
        subset_size=max(len(match.razorpay_ids), 1),
        has_ref_link=bool(has_ref),
        method_rank=_METHOD_RANK[match.method],
        narration_parsed=parsed,
    )


def raw_score(features: MatchFeatures) -> float:
    """Monotone in evidence strength. Never leaves [0, 1]."""
    score = 0.35 * (features.method_rank / 3)
    score += 0.25 / (1 + features.residual_paise / 10)
    score += 0.15 / (1 + features.date_gap_days)
    score += 0.10 / features.subset_size
    score += 0.10 if features.has_ref_link else 0.0
    score += 0.05 if features.narration_parsed else 0.0
    return max(0.0, min(1.0, score))
