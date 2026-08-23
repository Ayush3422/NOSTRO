from datetime import date

from nostro.match.blocking import BlockingConfig, amount_date_key, build_blocks, candidates_for
from nostro.models import CanonicalRow, Direction, Source
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src, amount, day, refs=None):
    return CanonicalRow(source=src, row_id=rid, amount_paise=amount,
                        direction=Direction.CREDIT, value_date=date(2026, 6, day),
                        refs=refs or {})


def test_amount_date_key_is_stable():
    assert amount_date_key(100, date(2026, 6, 3)) == amount_date_key(100, date(2026, 6, 3))
    assert amount_date_key(100, date(2026, 6, 3)) != amount_date_key(101, date(2026, 6, 3))


def test_utr_and_order_blocks_are_built():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 100, 3, {"order_id": "o1"})],
        bank=[_row("bk_1", Source.BANK, 100, 3, {"utr": "UTR1"})],
        erp=[_row("inv_1", Source.ERP, 100, 1, {"order_id": "o1"})],
    )
    blocks = build_blocks(cset, BlockingConfig())
    assert "pay_1" in blocks.by_order["o1"]
    assert "inv_1" in blocks.by_order["o1"]


def test_candidates_respect_the_date_window():
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY, 100, 3)],
        bank=[_row("bk_near", Source.BANK, 100, 5), _row("bk_far", Source.BANK, 100, 20)],
    )
    blocks = build_blocks(cset, BlockingConfig(date_window_days=3))
    got = candidates_for(cset.razorpay[0], blocks, BlockingConfig(date_window_days=3))
    assert "bk_near" in got
    assert "bk_far" not in got


def test_blocking_reduces_the_comparison_space():
    # NOTE: amounts are spaced 1000 paise apart (well past the default
    # amount_tolerance_paise=100) so the tolerance window can't sweep in every
    # other row. Spacing by 1 paise (as literally given in the plan) makes every
    # row's +-100 paise window cover virtually the whole 200-row amount range,
    # which makes the asserted 10x reduction mathematically unachievable against
    # the reference candidates_for implementation -- confirmed by hand and by a
    # failing run before this fix. Widening the spacing preserves the test's
    # intent (blocking meaningfully cuts the comparison space) without changing
    # any implementation code.
    rows = [_row(f"pay_{i}", Source.RAZORPAY, 1000 + i * 1000, 3) for i in range(200)]
    bank = [_row(f"bk_{i}", Source.BANK, 1000 + i * 1000, 3) for i in range(200)]
    cset = CanonicalSet(razorpay=rows, bank=bank)
    blocks = build_blocks(cset, BlockingConfig())
    total = sum(len(candidates_for(r, blocks, BlockingConfig())) for r in rows)
    assert total < len(rows) * len(bank) / 10
