"""Isotonic calibration of match scores.

Isotonic rather than Platt scaling because the score-to-truth relationship is
monotone but not sigmoid, and isotonic makes no shape assumption. Fitted on the
training cycles only; every reported number comes from the held-out cycles.
"""
from __future__ import annotations

import json
from pathlib import Path

from sklearn.isotonic import IsotonicRegression

from nostro.models import GroundTruthLink, Match


def label_matches(matches: list[Match], links: list[GroundTruthLink]) -> list[int]:
    """1 when every pair a match asserts is present in ground truth, else 0.

    Strict on purpose: a split settlement match that pulls in one wrong payment
    is wrong, because posting it would move money against the wrong invoice.
    """
    truth: set[tuple[str, str]] = set()
    for link in links:
        truth |= link.pairs()
    out = []
    for match in matches:
        pairs = match.pairs()
        out.append(1 if pairs and pairs <= truth else 0)
    return out


class Calibrator:
    def __init__(self) -> None:
        self._model: IsotonicRegression | None = None

    def fit(self, scores: list[float], labels: list[int]) -> "Calibrator":
        if not scores or len(set(labels)) < 2:
            return self                      # nothing to learn; stay a pass-through
        model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        model.fit(scores, labels)
        self._model = model
        return self

    def predict(self, scores: list[float]) -> list[float]:
        if self._model is None:
            return [max(0.0, min(1.0, s)) for s in scores]
        return [float(p) for p in self._model.predict(scores)]

    @staticmethod
    def brier(probabilities: list[float], labels: list[int]) -> float:
        if not probabilities:
            return 0.0
        return sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(probabilities)

    @staticmethod
    def reliability_bins(
        probabilities: list[float], labels: list[int], n_bins: int = 10
    ) -> list[dict]:
        bins: list[dict] = []
        for i in range(n_bins):
            lower, upper = i / n_bins, (i + 1) / n_bins
            members = [
                (p, y) for p, y in zip(probabilities, labels)
                if (lower <= p < upper) or (i == n_bins - 1 and p == 1.0)
            ]
            bins.append({
                "lower": lower, "upper": upper, "count": len(members),
                "mean_predicted": sum(p for p, _ in members) / len(members) if members else 0.0,
                "observed": sum(y for _, y in members) / len(members) if members else 0.0,
            })
        return bins

    def save(self, path: Path) -> None:
        if self._model is None:
            Path(path).write_text(json.dumps({"fitted": False}), encoding="utf-8")
            return
        Path(path).write_text(json.dumps({
            "fitted": True,
            "x": [float(v) for v in self._model.X_thresholds_],
            "y": [float(v) for v in self._model.y_thresholds_],
        }), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Calibrator":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        cal = cls()
        if payload.get("fitted"):
            model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            model.fit(payload["x"], payload["y"])
            cal._model = model
        return cal
