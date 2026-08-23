"""Measured evaluation against generator-owned labels.

No number produced here may be quoted anywhere without saying which split it
came from. The held-out split is by settlement cycle, never by row: splitting by
row would leak, because the other legs of the same split settlement would sit on
the training side.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from nostro.models import GroundTruthLink, Match
from nostro.normalize.canonical import CanonicalSet


class EvalReport(BaseModel):
    precision: float
    recall: float
    f1: float
    match_rate: float
    true_pairs: int
    predicted_pairs: int
    correct_pairs: int
    rows_evaluated: int
    unmatched_row_ids: list[str]
    elapsed_seconds: float
    rows_per_second: float

    def as_markdown_row(self, label: str) -> str:
        return (f"| {label} | {self.precision:.4f} | {self.recall:.4f} | "
                f"{self.f1:.4f} | {self.match_rate:.4f} | {self.rows_evaluated} | "
                f"{self.rows_per_second:,.0f} |")


def load_ground_truth(path: Path) -> list[GroundTruthLink]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [GroundTruthLink(**item) for item in payload]


def filter_to_cycles(
    links: list[GroundTruthLink], cset: CanonicalSet, cycles: tuple[str, ...]
) -> list[GroundTruthLink]:
    """Keep only links whose Razorpay leg falls in the given settlement cycles."""
    wanted = set(cycles)
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}
    return [
        link for link in links
        if any(cycle_of.get(rid) in wanted for rid in link.razorpay_ids)
    ]


def evaluate(
    matches: list[Match],
    links: list[GroundTruthLink],
    cset: CanonicalSet,
    elapsed_seconds: float,
) -> EvalReport:
    predicted: set[tuple[str, str]] = set()
    for match in matches:
        predicted |= match.pairs()

    truth: set[tuple[str, str]] = set()
    for link in links:
        truth |= link.pairs()

    correct = predicted & truth
    precision = len(correct) / len(predicted) if predicted else 0.0
    recall = len(correct) / len(truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    all_ids = {r.row_id for r in (*cset.razorpay, *cset.bank, *cset.erp)}
    touched: set[str] = set()
    for match in matches:
        touched.update(match.razorpay_ids)
        touched.update(match.bank_ids)
        touched.update(match.erp_ids)
    matched_rows = touched & all_ids

    rows = len(all_ids)
    return EvalReport(
        precision=precision, recall=recall, f1=f1,
        match_rate=len(matched_rows) / rows if rows else 0.0,
        true_pairs=len(truth), predicted_pairs=len(predicted), correct_pairs=len(correct),
        rows_evaluated=rows,
        unmatched_row_ids=sorted(all_ids - matched_rows),
        elapsed_seconds=elapsed_seconds,
        rows_per_second=(rows / elapsed_seconds) if elapsed_seconds > 0 else 0.0,
    )
