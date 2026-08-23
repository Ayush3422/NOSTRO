from datetime import date

from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.scoring import _METHOD_RANK, extract_features, raw_score
from nostro.models import CanonicalRow, Direction, Match, MatchMethod, Source
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src, amount, day, refs=None):
    return CanonicalRow(source=src, row_id=rid, amount_paise=amount,
                        direction=Direction.CREDIT, value_date=date(2026, 6, day),
                        refs=refs or {})


def _blocks(rows):
    return build_blocks(CanonicalSet(razorpay=[r for r in rows if r.source is Source.RAZORPAY],
                                     bank=[r for r in rows if r.source is Source.BANK],
                                     erp=[r for r in rows if r.source is Source.ERP]),
                        BlockingConfig())


def test_exact_same_day_match_scores_higher_than_a_drifted_late_one():
    rows = [_row("pay_1", Source.RAZORPAY, 5000, 3), _row("bk_1", Source.BANK, 5000, 3),
            _row("pay_2", Source.RAZORPAY, 5000, 3), _row("bk_2", Source.BANK, 5060, 6)]
    blocks = _blocks(rows)
    tight = Match(match_id="a", razorpay_ids=("pay_1",), bank_ids=("bk_1",),
                  score=0.0, method=MatchMethod.EXACT, residual_paise=0)
    loose = Match(match_id="b", razorpay_ids=("pay_2",), bank_ids=("bk_2",),
                  score=0.0, method=MatchMethod.TOLERANCE, residual_paise=60)
    assert raw_score(extract_features(tight, blocks)) > raw_score(extract_features(loose, blocks))


def test_features_capture_subset_size_and_date_gap():
    rows = [_row("pay_1", Source.RAZORPAY, 2000, 3), _row("pay_2", Source.RAZORPAY, 3000, 3),
            _row("bk_1", Source.BANK, 5000, 5)]
    m = Match(match_id="s", razorpay_ids=("pay_1", "pay_2"), bank_ids=("bk_1",),
              score=0.0, method=MatchMethod.SUBSET_SUM, residual_paise=0)
    f = extract_features(m, _blocks(rows))
    assert f.subset_size == 2
    assert f.date_gap_days == 2


def test_shared_order_id_is_recorded_as_a_ref_link():
    rows = [_row("pay_1", Source.RAZORPAY, 5000, 3, {"order_id": "o1"}),
            _row("inv_1", Source.ERP, 5000, 1, {"order_id": "o1"})]
    m = Match(match_id="r", razorpay_ids=("pay_1",), erp_ids=("inv_1",),
              score=0.0, method=MatchMethod.EXACT, residual_paise=0)
    assert extract_features(m, _blocks(rows)).has_ref_link is True


def test_score_stays_inside_the_unit_interval():
    rows = [_row("pay_1", Source.RAZORPAY, 5000, 3), _row("bk_1", Source.BANK, 9999, 20)]
    m = Match(match_id="x", razorpay_ids=("pay_1",), bank_ids=("bk_1",),
              score=0.0, method=MatchMethod.TOLERANCE, residual_paise=100000)
    s = raw_score(extract_features(m, _blocks(rows)))
    assert 0.0 <= s <= 1.0


def test_every_match_method_has_a_method_rank():
    for method in MatchMethod:
        assert method in _METHOD_RANK, f"{method} missing from _METHOD_RANK"


def test_reference_match_does_not_crash_extract_features():
    rows = [_row("pay_1", Source.RAZORPAY, 5000, 3, {"order_id": "o1"}),
            _row("inv_1", Source.ERP, 5000, 3, {"order_id": "o1"})]
    m = Match(match_id="ref", razorpay_ids=("pay_1",), erp_ids=("inv_1",),
              score=0.0, method=MatchMethod.REFERENCE, residual_paise=0)
    f = extract_features(m, _blocks(rows))
    assert f.method_rank == 3
