from datetime import date
from nostro.models import (
    CanonicalRow, Direction, GroundTruthLink, Match, MatchMethod, ParsedBy, Source,
)


def _row(row_id: str, source: Source) -> CanonicalRow:
    return CanonicalRow(
        source=source, row_id=row_id, amount_paise=100,
        direction=Direction.CREDIT, value_date=date(2026, 8, 1),
    )


def test_row_defaults():
    row = _row("rp_1", Source.RAZORPAY)
    assert row.refs == {}
    assert row.parsed_by is ParsedBy.NONE
    assert row.narration_raw is None


def test_float_amount_rejected():
    import pytest
    with pytest.raises(Exception):
        CanonicalRow(
            source=Source.BANK, row_id="b1", amount_paise=10.5,
            direction=Direction.CREDIT, value_date=date(2026, 8, 1),
        )


def test_match_pairs_are_cross_source_and_unordered():
    m = Match(
        match_id="m1", razorpay_ids=("rp_1", "rp_2"), bank_ids=("bk_1",),
        erp_ids=(), score=1.0, probability=0.9, method=MatchMethod.SUBSET_SUM,
    )
    assert m.pairs() == {("bk_1", "rp_1"), ("bk_1", "rp_2")}


def test_ground_truth_pairs_match_the_same_shape():
    link = GroundTruthLink(
        link_id="gt1", razorpay_ids=("rp_1", "rp_2"), bank_ids=("bk_1",), erp_ids=(),
    )
    assert link.pairs() == {("bk_1", "rp_1"), ("bk_1", "rp_2")}


def test_single_source_group_asserts_no_pairs():
    link = GroundTruthLink(link_id="gt2", razorpay_ids=("rp_9",), bank_ids=(), erp_ids=())
    assert link.pairs() == set()
