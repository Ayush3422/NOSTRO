"""N:M matching via bounded subset-sum.

A settlement credit is the net of one or more payments. Recovering which ones is
subset-sum, which is NP-complete in general — so we bound it honestly rather than
pretending otherwise: at most `max_candidates` candidates drawn from the blocking
window, at most `max_subset_size` per subset. Everything outside those bounds is
reported as an exception, not silently guessed at.

Depth-first with two prunes: candidates are sorted descending so large values are
placed first, and a running suffix-sum bound abandons a branch that can no longer
reach the target.
"""
from __future__ import annotations

from pydantic import BaseModel

from nostro.match.blocking import Blocks
from nostro.models import CanonicalRow, Match, MatchMethod
from nostro.normalize.canonical import CanonicalSet


class SolverConfig(BaseModel):
    max_subset_size: int = 6
    max_candidates: int = 40
    residual_tolerance_paise: int = 100
    date_window_days: int = 3


def _ordered(candidates: list[CanonicalRow], limit: int) -> list[CanonicalRow]:
    ordered = sorted(candidates, key=lambda r: (-r.amount_paise, r.row_id))
    return ordered[:limit]


def find_subset(
    target_paise: int, candidates: list[CanonicalRow], cfg: SolverConfig
) -> tuple[tuple[str, ...], int] | None:
    """Smallest subset whose sum is within tolerance of target. None if none fits."""
    pool = _ordered(candidates, cfg.max_candidates)
    amounts = [r.amount_paise for r in pool]
    n = len(pool)

    # suffix[i] = sum of amounts[i:], used to abandon hopeless branches early.
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + amounts[i]

    best: tuple[int, int, tuple[str, ...]] | None = None  # (residual, size, ids)

    def consider(chosen: list[int], total: int) -> None:
        nonlocal best
        residual = abs(target_paise - total)
        if residual > cfg.residual_tolerance_paise:
            return
        ids = tuple(sorted(pool[i].row_id for i in chosen))
        key = (residual, len(chosen), ids)
        if best is None or key < best:
            best = key

    def search(start: int, chosen: list[int], total: int) -> None:
        if chosen:
            consider(chosen, total)
        if len(chosen) == cfg.max_subset_size:
            return
        for i in range(start, n):
            if total + suffix[i] < target_paise - cfg.residual_tolerance_paise:
                return                      # even taking everything left falls short
            if total + amounts[i] > target_paise + cfg.residual_tolerance_paise:
                continue                    # this one overshoots; smaller ones may not
            chosen.append(i)
            search(i + 1, chosen, total + amounts[i])
            chosen.pop()

    search(0, [], 0)
    if best is None:
        return None
    residual, _size, ids = best
    return ids, residual


def match_subset_sums(
    cset: CanonicalSet, blocks: Blocks, consumed: set[str], cfg: SolverConfig
) -> list[Match]:
    used = set(consumed)
    matches: list[Match] = []

    bank_rows = sorted(
        (r for r in cset.bank if r.row_id not in used),
        key=lambda r: (-r.amount_paise, r.row_id),
    )

    for bank_row in bank_rows:
        if bank_row.row_id in used:
            continue

        window = [
            r for r in cset.razorpay
            if r.row_id not in used
            and r.direction is bank_row.direction
            and abs((r.value_date - bank_row.value_date).days) <= cfg.date_window_days
            and r.amount_paise <= bank_row.amount_paise + cfg.residual_tolerance_paise
        ]
        if len(window) < 2:
            continue                        # 1:1 is Task 8's job, not ours

        found = find_subset(bank_row.amount_paise, window, cfg)
        if found is None:
            continue
        ids, residual = found
        if len(ids) < 2:
            continue                        # a singleton is a 1:1 match, not a split

        matches.append(Match(
            match_id=f"ss_{bank_row.row_id}",
            razorpay_ids=ids, bank_ids=(bank_row.row_id,), erp_ids=(),
            score=1.0 - min(residual, cfg.residual_tolerance_paise)
                  / max(cfg.residual_tolerance_paise, 1) * 0.2,
            method=MatchMethod.SUBSET_SUM, residual_paise=residual,
        ))
        used.add(bank_row.row_id)
        used.update(ids)

    return matches
