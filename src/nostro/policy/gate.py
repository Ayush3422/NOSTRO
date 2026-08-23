"""Auto-post gating.

The threshold is not a vibe. Above tau we post automatically and eat the cost of
being wrong; below tau we pay an analyst to look. Sweeping tau over the observed
probabilities and taking the minimum of

    cost(tau) = wrong_post_cost * false_positives(tau)
              + review_cost * reviewed(tau)

gives a threshold with a defensible reason attached. Both cost inputs are stated
assumptions, and the README labels them as such rather than presenting them as
measured facts.

No model is involved in this decision, by design.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Decision(str, Enum):
    AUTO_POST = "auto_post"
    REVIEW = "review"


class CostModel(BaseModel):
    wrong_post_cost_paise: int = 250000     # Rs 2,500 to unwind a bad posting
    review_cost_paise: int = 5000           # Rs 50 of analyst time per review


class ThresholdChoice(BaseModel):
    tau: float
    expected_cost_paise: int
    auto_post_count: int
    precision_at_tau: float
    curve: list[dict]


def decide(probability: float, tau: float) -> Decision:
    return Decision.AUTO_POST if probability >= tau else Decision.REVIEW


def choose_tau(
    probabilities: list[float], labels: list[int], costs: CostModel
) -> ThresholdChoice:
    if not probabilities:
        return ThresholdChoice(tau=1.0, expected_cost_paise=0, auto_post_count=0,
                               precision_at_tau=0.0, curve=[])

    candidates = sorted({round(p, 4) for p in probabilities} | {1.0})
    curve: list[dict] = []

    for tau in candidates:
        posted = [(p, y) for p, y in zip(probabilities, labels) if p >= tau]
        reviewed = len(probabilities) - len(posted)
        false_positives = sum(1 for _p, y in posted if y == 0)
        cost = (costs.wrong_post_cost_paise * false_positives
                + costs.review_cost_paise * reviewed)
        precision = (sum(y for _p, y in posted) / len(posted)) if posted else 0.0
        curve.append({"tau": tau, "expected_cost_paise": cost,
                      "auto_post_count": len(posted), "precision": precision})

    best = min(curve, key=lambda point: (point["expected_cost_paise"], point["tau"]))
    return ThresholdChoice(
        tau=best["tau"], expected_cost_paise=best["expected_cost_paise"],
        auto_post_count=best["auto_post_count"],
        precision_at_tau=best["precision"], curve=curve,
    )
