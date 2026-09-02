import random
from pathlib import Path

from nostro.match.calibrate import Calibrator, label_matches
from nostro.models import GroundTruthLink, Match, MatchMethod


def _synthetic(n=400):
    rng = random.Random(0)
    scores, labels = [], []
    for _ in range(n):
        s = rng.random()
        scores.append(s)
        labels.append(1 if rng.random() < s else 0)   # true rate equals the score
    return scores, labels


def test_calibration_improves_the_brier_score():
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    before = Calibrator.brier(scores, labels)
    after = Calibrator.brier(cal.predict(scores), labels)
    assert after <= before


def test_predictions_stay_probabilities():
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    assert all(0.0 <= p <= 1.0 for p in cal.predict([0.0, 0.5, 1.0, -5.0, 7.0]))


def test_calibration_is_monotone_non_decreasing():
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    preds = cal.predict([0.1, 0.3, 0.5, 0.7, 0.9])
    assert preds == sorted(preds)


def test_reliability_bins_cover_the_range():
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    bins = cal.reliability_bins(cal.predict(scores), labels, n_bins=5)
    assert len(bins) == 5
    assert all({"lower", "upper", "count", "mean_predicted", "observed"} <= set(b) for b in bins)


def test_an_unfitted_calibrator_passes_scores_through():
    assert Calibrator().predict([0.4]) == [0.4]


def test_calibrator_round_trips_to_disk(tmp_path: Path):
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    p = tmp_path / "cal.json"
    cal.save(p)
    assert Calibrator.load(p).predict([0.5]) == cal.predict([0.5])


def test_label_is_one_only_when_every_asserted_pair_is_true():
    links = [GroundTruthLink(link_id="g", razorpay_ids=("pay_1", "pay_2"),
                             bank_ids=("bk_1",))]
    good = Match(match_id="a", razorpay_ids=("pay_1", "pay_2"), bank_ids=("bk_1",),
                 score=1.0, method=MatchMethod.SUBSET_SUM)
    partly_wrong = Match(match_id="b", razorpay_ids=("pay_1", "pay_9"), bank_ids=("bk_1",),
                         score=1.0, method=MatchMethod.SUBSET_SUM)
    assert label_matches([good, partly_wrong], links) == [1, 0]


def test_fit_with_fewer_than_two_distinct_labels_stays_pass_through():
    cal = Calibrator().fit([0.1, 0.5, 0.9], [1, 1, 1])
    assert cal.predict([0.3]) == [0.3]
    assert cal.is_fitted is False  # callers must be able to detect this -- R47


def test_a_normal_fit_reports_is_fitted():
    scores, labels = _synthetic()
    cal = Calibrator().fit(scores, labels)
    assert cal.is_fitted is True
