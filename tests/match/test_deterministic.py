from datetime import date

from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.deterministic import match_deterministic
from nostro.models import CanonicalRow, Direction, MatchMethod, Source
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src, amount, day, refs=None):
    return CanonicalRow(source=src, row_id=rid, amount_paise=amount,
                        direction=Direction.CREDIT, value_date=date(2026, 6, day),
                        refs=refs or {})


def _run(cset, cfg=None):
    cfg = cfg or BlockingConfig()
    return match_deterministic(cset, build_blocks(cset, cfg), cfg)


def test_exact_amount_and_date_matches():
    cset = CanonicalSet(razorpay=[_row("pay_1", Source.RAZORPAY, 97640, 3)],
                        bank=[_row("bk_1", Source.BANK, 97640, 3)])
    result = _run(cset)
    assert len(result.matches) == 1
    assert result.matches[0].method is MatchMethod.EXACT
    assert result.matches[0].pairs() == {("bk_1", "pay_1")}


def test_one_paise_drift_is_a_tolerance_match_not_an_exact_one():
    cset = CanonicalSet(razorpay=[_row("pay_1", Source.RAZORPAY, 97640, 3)],
                        bank=[_row("bk_1", Source.BANK, 97641, 3)])
    result = _run(cset)
    assert len(result.matches) == 1
    assert result.matches[0].method is MatchMethod.TOLERANCE
    assert result.matches[0].residual_paise == 1


def test_exact_wins_over_tolerance_globally():
    # bk_1 is an exact partner for pay_exact and a tolerance partner for pay_drift.
    # The exact pairing must win, leaving pay_drift unmatched rather than stealing bk_1.
    cset = CanonicalSet(
        razorpay=[_row("pay_drift", Source.RAZORPAY, 99999, 3),
                  _row("pay_exact", Source.RAZORPAY, 100000, 3)],
        bank=[_row("bk_1", Source.BANK, 100000, 3)],
    )
    result = _run(cset)
    assert len(result.matches) == 1
    assert result.matches[0].pairs() == {("bk_1", "pay_exact")}
    assert "pay_drift" not in result.consumed


def test_a_row_is_never_consumed_twice():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 500, 3), _row("pay_2", Source.RAZORPAY, 500, 3)],
        bank=[_row("bk_1", Source.BANK, 500, 3)],
    )
    result = _run(cset)
    assert len(result.matches) == 1
    assert len(result.consumed) == 2


def test_opposite_directions_never_match():
    debit = CanonicalRow(source=Source.BANK, row_id="bk_1", amount_paise=500,
                         direction=Direction.DEBIT, value_date=date(2026, 6, 3))
    cset = CanonicalSet(razorpay=[_row("pay_1", Source.RAZORPAY, 500, 3)], bank=[debit])
    assert _run(cset).matches == []


def test_erp_matches_by_order_id():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 97640, 3, {"order_id": "o1"})],
        erp=[_row("inv_1", Source.ERP, 100000, 1, {"order_id": "o1"})],
    )
    result = _run(cset)
    assert any(m.pairs() == {("inv_1", "pay_1")} for m in result.matches)
