"""Candidate generation.

Comparing every Razorpay row against every bank row is quadratic and pointless:
5,000 rows is 25 million comparisons, almost all of them absurd. Blocking cuts
that to the handful of rows that could plausibly relate, which is what makes the
throughput number in EVALUATION.md worth printing.
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from nostro.models import CanonicalRow
from nostro.normalize.canonical import CanonicalSet


class BlockingConfig(BaseModel):
    date_window_days: int = 3
    amount_tolerance_paise: int = 100
    amount_bucket_paise: int = 100


def amount_date_key(amount_paise: int, value_date: date) -> str:
    return f"{amount_paise}|{value_date.isoformat()}"


class Blocks(BaseModel):
    by_order: dict[str, list[str]] = {}
    by_utr: dict[str, list[str]] = {}
    by_amount_date: dict[str, list[str]] = {}
    row_index: dict[str, CanonicalRow] = {}


def build_blocks(cset: CanonicalSet, cfg: BlockingConfig) -> Blocks:
    blocks = Blocks()
    for row in (*cset.razorpay, *cset.bank, *cset.erp):
        blocks.row_index[row.row_id] = row
        order = row.refs.get("order_id")
        if order:
            blocks.by_order.setdefault(order, []).append(row.row_id)
        utr = row.refs.get("utr")
        if utr:
            blocks.by_utr.setdefault(utr, []).append(row.row_id)
        key = amount_date_key(row.amount_paise, row.value_date)
        blocks.by_amount_date.setdefault(key, []).append(row.row_id)
    return blocks


def candidates_for(row: CanonicalRow, blocks: Blocks, cfg: BlockingConfig) -> list[str]:
    """Row ids from other sources that could plausibly relate to `row`."""
    found: set[str] = set()

    order = row.refs.get("order_id")
    if order:
        found.update(blocks.by_order.get(order, ()))
    utr = row.refs.get("utr")
    if utr:
        found.update(blocks.by_utr.get(utr, ()))

    for day_offset in range(-cfg.date_window_days, cfg.date_window_days + 1):
        when = row.value_date + timedelta(days=day_offset)
        low = row.amount_paise - cfg.amount_tolerance_paise
        high = row.amount_paise + cfg.amount_tolerance_paise
        for amount in range(low, high + 1):
            found.update(blocks.by_amount_date.get(amount_date_key(amount, when), ()))

    found.discard(row.row_id)
    return [
        rid for rid in found
        if blocks.row_index[rid].source is not row.source
        and blocks.row_index[rid].direction is row.direction
    ]
