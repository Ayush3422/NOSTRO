import json
from datetime import date
from pathlib import Path

from nostro.eval.harness import evaluate, load_ground_truth
from nostro.models import (
    CanonicalRow, Direction, GroundTruthLink, Match, MatchMethod, Source,
)
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src):
    return CanonicalRow(source=src, row_id=rid, amount_paise=100,
                        direction=Direction.CREDIT, value_date=date(2026, 6, 1))


def _cset():
    return CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY), _row("pay_2", Source.RAZORPAY)],
        bank=[_row("bk_1", Source.BANK), _row("bk_2", Source.BANK)],
    )


def _match(mid, rp, bk, method=MatchMethod.EXACT):
    return Match(match_id=mid, razorpay_ids=tuple(rp), bank_ids=tuple(bk),
                 score=1.0, method=method)


def test_perfect_prediction_scores_one():
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",))]
    report = evaluate([_match("m1", ["pay_1"], ["bk_1"])], links, _cset(), 1.0)
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.f1 == 1.0


def test_a_wrong_pair_costs_precision_not_recall():
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",))]
    matches = [_match("m1", ["pay_1"], ["bk_1"]), _match("m2", ["pay_2"], ["bk_2"])]
    report = evaluate(matches, links, _cset(), 1.0)
    assert report.precision == 0.5
    assert report.recall == 1.0


def test_a_missed_pair_costs_recall_not_precision():
    links = [
        GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",)),
        GroundTruthLink(link_id="g2", razorpay_ids=("pay_2",), bank_ids=("bk_2",)),
    ]
    report = evaluate([_match("m1", ["pay_1"], ["bk_1"])], links, _cset(), 1.0)
    assert report.precision == 1.0
    assert report.recall == 0.5


def test_partial_credit_on_a_split_settlement():
    # Truth links three payments to one credit; we found two of the three.
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1", "pay_2", "pay_3"),
                             bank_ids=("bk_1",))]
    cset = CanonicalSet(
        razorpay=[_row("pay_1", Source.RAZORPAY), _row("pay_2", Source.RAZORPAY),
                  _row("pay_3", Source.RAZORPAY)],
        bank=[_row("bk_1", Source.BANK)],
    )
    report = evaluate([_match("m1", ["pay_1", "pay_2"], ["bk_1"],
                              MatchMethod.SUBSET_SUM)], links, cset, 1.0)
    assert report.precision == 1.0
    assert round(report.recall, 4) == round(2 / 3, 4)


def test_match_rate_counts_rows_touched_not_pairs():
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",))]
    report = evaluate([_match("m1", ["pay_1"], ["bk_1"])], links, _cset(), 1.0)
    assert report.match_rate == 0.5           # 2 of 4 rows appear in a match
    assert set(report.unmatched_row_ids) == {"pay_2", "bk_2"}


def test_empty_prediction_does_not_divide_by_zero():
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",))]
    report = evaluate([], links, _cset(), 1.0)
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0


def test_throughput_is_reported():
    links = [GroundTruthLink(link_id="g1", razorpay_ids=("pay_1",), bank_ids=("bk_1",))]
    report = evaluate([_match("m1", ["pay_1"], ["bk_1"])], links, _cset(), 2.0)
    assert report.rows_per_second == 2.0      # 4 rows / 2.0 s


def test_ground_truth_round_trips_from_disk(tmp_path: Path):
    p = tmp_path / "gt.json"
    p.write_text(json.dumps([{"link_id": "g1", "razorpay_ids": ["pay_1"],
                              "bank_ids": ["bk_1"], "erp_ids": []}]), encoding="utf-8")
    links = load_ground_truth(p)
    assert links[0].pairs() == {("bk_1", "pay_1")}
