from datetime import date

from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.solver import SolverConfig, find_subset, match_subset_sums
from nostro.models import CanonicalRow, Direction, MatchMethod, Source
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src, amount, day=3, cycle=None):
    return CanonicalRow(source=src, row_id=rid, amount_paise=amount,
                        direction=Direction.CREDIT, value_date=date(2026, 6, day),
                        settlement_cycle=cycle)


def test_finds_an_exact_three_way_split():
    cands = [_row("pay_1", Source.RAZORPAY, 3000),
             _row("pay_2", Source.RAZORPAY, 2000),
             _row("pay_3", Source.RAZORPAY, 5000)]
    found = find_subset(10000, cands, SolverConfig())
    assert found is not None
    ids, residual = found
    assert set(ids) == {"pay_1", "pay_2", "pay_3"}
    assert residual == 0


def test_prefers_the_smaller_subset_at_equal_residual():
    cands = [_row("pay_big", Source.RAZORPAY, 5000),
             _row("pay_a", Source.RAZORPAY, 2000),
             _row("pay_b", Source.RAZORPAY, 3000)]
    ids, residual = find_subset(5000, cands, SolverConfig())
    assert ids == ("pay_big",)
    assert residual == 0


def test_accepts_a_subset_within_residual_tolerance():
    cands = [_row("pay_1", Source.RAZORPAY, 3000), _row("pay_2", Source.RAZORPAY, 2001)]
    ids, residual = find_subset(5000, cands, SolverConfig(residual_tolerance_paise=100))
    assert set(ids) == {"pay_1", "pay_2"}
    assert residual == 1


def test_returns_none_when_nothing_is_close_enough():
    cands = [_row("pay_1", Source.RAZORPAY, 100), _row("pay_2", Source.RAZORPAY, 200)]
    assert find_subset(999999, cands, SolverConfig()) is None


def test_respects_max_subset_size():
    cands = [_row(f"pay_{i}", Source.RAZORPAY, 100) for i in range(10)]
    assert find_subset(1000, cands, SolverConfig(max_subset_size=3)) is None


def test_is_deterministic_across_input_orderings():
    cands = [_row("pay_1", Source.RAZORPAY, 3000), _row("pay_2", Source.RAZORPAY, 2000),
             _row("pay_3", Source.RAZORPAY, 5000)]
    first = find_subset(5000, cands, SolverConfig())
    second = find_subset(5000, list(reversed(cands)), SolverConfig())
    assert first == second


def test_end_to_end_split_is_matched_and_typed():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 3000, cycle="cyc_1"),
                  _row("pay_2", Source.RAZORPAY, 2000, cycle="cyc_1")],
        bank=[_row("bk_1", Source.BANK, 5000)],
    )
    blocks = build_blocks(cset, BlockingConfig())
    matches = match_subset_sums(cset, blocks, consumed=set(), cfg=SolverConfig())
    assert len(matches) == 1
    assert matches[0].method is MatchMethod.SUBSET_SUM
    assert matches[0].pairs() == {("bk_1", "pay_1"), ("bk_1", "pay_2")}


def test_a_subset_spanning_two_settlement_cycles_is_refused():
    # pay_1 (cyc_1) + pay_2 (cyc_2) sum EXACTLY to bk_1, but a settlement batch is
    # one cycle — a subset that mixes cycles must never be proposed, even when the
    # amounts line up perfectly. That is the whole point of the cycle constraint.
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 3000, cycle="cyc_1"),
                  _row("pay_2", Source.RAZORPAY, 2000, cycle="cyc_2")],
        bank=[_row("bk_1", Source.BANK, 5000)],
    )
    blocks = build_blocks(cset, BlockingConfig())
    assert match_subset_sums(cset, blocks, consumed=set(), cfg=SolverConfig()) == []


def test_already_consumed_rows_are_never_reused():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 3000), _row("pay_2", Source.RAZORPAY, 2000)],
        bank=[_row("bk_1", Source.BANK, 5000)],
    )
    blocks = build_blocks(cset, BlockingConfig())
    matches = match_subset_sums(cset, blocks, consumed={"pay_1"}, cfg=SolverConfig())
    assert matches == []


def test_debits_and_credits_are_never_mixed_in_one_subset():
    debit = CanonicalRow(source=Source.RAZORPAY, row_id="rfnd_1", amount_paise=2000,
                         direction=Direction.DEBIT, value_date=date(2026, 6, 3))
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 3000), debit],
        bank=[_row("bk_1", Source.BANK, 5000)],
    )
    blocks = build_blocks(cset, BlockingConfig())
    assert match_subset_sums(cset, blocks, consumed=set(), cfg=SolverConfig()) == []
