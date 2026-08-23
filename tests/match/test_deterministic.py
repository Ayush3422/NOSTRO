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
    # Razorpay canonicalises on net, ERP on gross, so a genuine order-id match
    # routinely carries a residual of exactly fee+GST. That is not amount
    # evidence, so it must be labelled REFERENCE, not EXACT, and the residual
    # is expected and informative rather than an error.
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 97640, 3, {"order_id": "o1"})],
        erp=[_row("inv_1", Source.ERP, 100000, 1, {"order_id": "o1"})],
    )
    result = _run(cset)
    matches = [m for m in result.matches if m.pairs() == {("inv_1", "pay_1")}]
    assert len(matches) == 1
    assert matches[0].method is MatchMethod.REFERENCE
    assert matches[0].residual_paise == 2360


def test_reference_pass_respects_direction():
    # Refunds and chargebacks inherit the original payment's order_id, so a
    # Razorpay DEBIT (a chargeback) can share an order_id block with the
    # payment's ERP invoice CREDIT. cb_1 sorts before pay_1, so under
    # row-id-sorted iteration without a direction filter the chargeback would
    # reach the block first and wrongly claim the invoice.
    debit = CanonicalRow(source=Source.RAZORPAY, row_id="cb_1", amount_paise=97640,
                         direction=Direction.DEBIT, value_date=date(2026, 6, 3),
                         refs={"order_id": "o1"})
    credit = _row("pay_1", Source.RAZORPAY, 97640, 3, {"order_id": "o1"})
    invoice = _row("inv_1", Source.ERP, 100000, 1, {"order_id": "o1"})
    cset = CanonicalSet(razorpay=[debit, credit], erp=[invoice])
    result = _run(cset)
    matches = [m for m in result.matches if "inv_1" in m.erp_ids]
    assert len(matches) == 1
    assert matches[0].pairs() == {("inv_1", "pay_1")}
    assert "cb_1" not in result.consumed


def test_erp_axis_match_leaves_row_available_for_bank_axis():
    # A payment claimed by pass 1 (reference identity, Razorpay<->ERP on order_id)
    # must still be reachable by pass 2 (exact amount, Razorpay<->bank). Three-way
    # reconciliation means the same payment legitimately sits on both axes; a
    # single global `consumed` set would let pass 1 starve pass 2 of a row it has
    # every right to use — exactly the bug the subset-sum solver surfaced on the
    # real dataset.
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 97640, 3, {"order_id": "o1"})],
        bank=[_row("bk_1", Source.BANK, 97640, 3)],
        erp=[_row("inv_1", Source.ERP, 100000, 1, {"order_id": "o1"})],
    )
    result = _run(cset)
    methods = {m.method for m in result.matches}
    assert methods == {MatchMethod.REFERENCE, MatchMethod.EXACT}
    assert any(m.pairs() == {("inv_1", "pay_1")} for m in result.matches)
    assert any(m.pairs() == {("bk_1", "pay_1")} for m in result.matches)
    assert "pay_1" in result.bank_consumed
    assert "pay_1" in result.consumed


def test_bank_axis_match_leaves_row_available_for_erp_axis():
    # Same claim, the other direction: a payment already claimed on the bank axis
    # (passes 2-3) must still surface its independent ERP reference match (pass 1).
    # Both matches existing side by side is the point — the two axes must not
    # compete for the same row.
    cset = CanonicalSet(
        razorpay=[_row("pay_2", Source.RAZORPAY, 50000, 3, {"order_id": "o2"})],
        bank=[_row("bk_2", Source.BANK, 50000, 3)],
        erp=[_row("inv_2", Source.ERP, 52000, 2, {"order_id": "o2"})],
    )
    result = _run(cset)
    ref_matches = [m for m in result.matches if m.method is MatchMethod.REFERENCE]
    bank_matches = [m for m in result.matches if m.method is MatchMethod.EXACT]
    assert len(ref_matches) == 1
    assert ref_matches[0].pairs() == {("inv_2", "pay_2")}
    assert len(bank_matches) == 1
    assert bank_matches[0].pairs() == {("bk_2", "pay_2")}
    assert "pay_2" in result.bank_consumed
    assert "pay_2" in result.consumed
    assert "inv_2" in result.consumed
    assert "inv_2" not in result.bank_consumed
