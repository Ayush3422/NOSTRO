"""Smoke test for Task 12: choose_tau on real, in-sample data.

This fits the calibrator and chooses tau on the SAME data (no train/held-out
split — that lands in a later task). Treat these numbers as a check that the
mechanism works end to end, not as a published result.
"""
from pathlib import Path
from time import perf_counter

from nostro.eval.harness import load_ground_truth
from nostro.ingest.loader import load_csv
from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.calibrate import Calibrator, label_matches
from nostro.match.deterministic import match_deterministic
from nostro.match.scoring import extract_features, raw_score
from nostro.match.solver import SolverConfig, match_subset_sums
from nostro.models import Source
from nostro.normalize.canonical import CanonicalSet, to_canonical
from nostro.normalize.narration_parser import NarrationParser
from nostro.policy.gate import CostModel, choose_tau

data = Path("data/full")
parser = NarrationParser()

rp = load_csv(data / "razorpay_settlement.csv", Source.RAZORPAY)
bk = load_csv(data / "bank_statement.csv", Source.BANK)
erp = load_csv(data / "erp_sales.csv", Source.ERP)

cset = CanonicalSet(
    razorpay=to_canonical(rp.rows, Source.RAZORPAY),
    bank=to_canonical(bk.rows, Source.BANK, parser),
    erp=to_canonical(erp.rows, Source.ERP),
)

started = perf_counter()
cfg = BlockingConfig()
blocks = build_blocks(cset, cfg)
result = match_deterministic(cset, blocks, cfg)
solver_matches = match_subset_sums(cset, blocks, result.bank_consumed, SolverConfig())
all_matches = result.matches + solver_matches
elapsed = perf_counter() - started

links = load_ground_truth(data / "ground_truth.json")
labels = label_matches(all_matches, links)

raw_scores = [raw_score(extract_features(m, blocks)) for m in all_matches]
cal = Calibrator().fit(raw_scores, labels)
probabilities = cal.predict(raw_scores)

choice = choose_tau(probabilities, labels, CostModel())

print(f"matches           {len(all_matches)}")
print(f"positives (labels) {sum(labels)}")
print(f"elapsed            {elapsed:.2f}s")
print()
print(f"chosen tau         {choice.tau}")
print(f"precision at tau   {choice.precision_at_tau:.4f}")
print(f"auto_post_count    {choice.auto_post_count}")
print(f"expected cost      Rs {choice.expected_cost_paise / 100:,.2f}")
print()
print("representative curve points:")
curve = sorted(choice.curve, key=lambda p: p["tau"])
step = max(1, len(curve) // 10)
for point in curve[::step]:
    print(f"  tau={point['tau']:.4f} cost=Rs{point['expected_cost_paise']/100:,.2f} "
          f"auto_post={point['auto_post_count']} precision={point['precision']:.4f}")
print(f"  tau={curve[-1]['tau']:.4f} cost=Rs{curve[-1]['expected_cost_paise']/100:,.2f} "
      f"auto_post={curve[-1]['auto_post_count']} precision={curve[-1]['precision']:.4f}")
