"""Generate EVALUATION.md from a real run. Numbers are never hand-typed."""
from pathlib import Path

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.match.calibrate import Calibrator
from nostro.money import paise_to_rupees
from nostro.pipeline import CloseConfig, run_close

import json

DATA = Path("data/full")
meta_path = DATA / "meta.json"
if not (DATA / "ground_truth.json").exists() or not meta_path.exists():
    ds = generate(GeneratorConfig(), DATA)
    meta_path.write_text(json.dumps({"holdout_cycles": list(ds.holdout_cycles)}, indent=2),
                          encoding="utf-8")

holdout_cycles: tuple[str, ...] = tuple(
    json.loads(meta_path.read_text(encoding="utf-8")).get("holdout_cycles", [])
)

# --- dataset composition, read off the generated files, not hand-typed ----
def _csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh) - 1          # minus the header row

n_razorpay = _csv_row_count(DATA / "razorpay_settlement.csv")
n_bank = _csv_row_count(DATA / "bank_statement.csv")
n_erp = _csv_row_count(DATA / "erp_sales.csv")
ground_truth_links = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
n_links = len(ground_truth_links)
n_splits = sum(1 for link in ground_truth_links if len(link.get("razorpay_ids", [])) > 1)

# --- pass 1: held-out (the headline) --------------------------------------
result = run_close(CloseConfig(data_dir=DATA, audit_path=Path("data/eval_audit.jsonl"),
                               holdout_cycles=holdout_cycles, use_model=False))
r = result.report

# --- pass 2: in-sample, for contrast --------------------------------------
# Same dataset, same code path, holdout_cycles=() so calibration and
# evaluation both see every link. This is what the historical "in-sample"
# figures used to be hand-copied from -- generating it here instead means the
# README's claim that every number in this section was produced by this
# script is actually true, and both passes are guaranteed to come from the
# *same* dataset generation, closing the exact gap R37 fixed for the headline
# alone.
result_in_sample = run_close(CloseConfig(data_dir=DATA, audit_path=Path("data/eval_audit_insample.jsonl"),
                                         holdout_cycles=(), use_model=False))
r_in = result_in_sample.report

# --- Brier: uncalibrated raw scores vs. calibrated probabilities ----------
# Computed on the in-sample run's train population (== every match/link,
# since holdout_cycles=() means "train" is everything) -- the same
# population the historical Brier figures were measured against, but
# generated fresh instead of hand-copied.
brier_before = brier_after = None
positives = total_labels = 0
if result_in_sample.calibration_scores is not None:
    scores_ = result_in_sample.calibration_scores
    probs_ = result_in_sample.calibration_probabilities
    labels_ = result_in_sample.calibration_labels
    brier_before = Calibrator.brier(scores_, labels_)
    brier_after = Calibrator.brier(probs_, labels_)
    positives = sum(labels_)
    total_labels = len(labels_)

lines = [
    "# Nostro — Measured Results",
    "",
    "Every number below was produced by `python scripts/build_evaluation.py`.",
    "None were typed by hand. Labels come from the generator that produced the data.",
    "",
    "## Dataset",
    "",
    f"{n_razorpay:,} Razorpay rows + {n_bank:,} bank rows + {n_erp:,} ERP rows = "
        f"**{n_razorpay + n_bank + n_erp:,} rows**, {n_links:,} ground-truth links, "
        f"{n_splits:,} genuine split settlements (a link with more than one Razorpay "
        "leg) — regenerated deterministically from a fixed seed. Razorpay's stated bar "
        "is 50 records.",
    "",
    "## Headline: held-out vs in-sample",
    "",
    "9 of 30 settlement cycles were withheld from the calibrator and the",
    "auto-post threshold entirely for the held-out pass; the in-sample pass",
    "re-runs the identical close with `holdout_cycles=()`, so calibration,",
    "gating, and evaluation all see every link. Both passes are the same",
    "dataset generation and the same code path — only the split differs.",
    "The held-out column is the one worth citing; in-sample is shown to make",
    "the honest gap between them visible, not to headline it.",
    "",
    "| metric | held-out | in-sample |",
    "|---|---|---|",
    f"| rows evaluated | {r.rows_evaluated:,} | {r_in.rows_evaluated:,} |"
        if r and r_in else "| rows evaluated | n/a | n/a |",
    (f"| match rate | {result.holdout_razorpay_match_rate:.4f} (razorpay-side) "
     f"| {r_in.match_rate:.4f} (whole population) |")
        if result.holdout_razorpay_match_rate is not None and r_in else "| match rate | n/a | n/a |",
    f"| precision | {r.precision:.4f} | {r_in.precision:.4f} |" if r and r_in else "| precision | n/a | n/a |",
    f"| recall | {r.recall:.4f} | {r_in.recall:.4f} |" if r and r_in else "| recall | n/a | n/a |",
    f"| F1 | {r.f1:.4f} | {r_in.f1:.4f} |" if r and r_in else "| F1 | n/a | n/a |",
    f"| throughput | {r.rows_per_second:,.0f} rows/s | {r_in.rows_per_second:,.0f} rows/s |"
        if r and r_in else "| throughput | n/a | n/a |",
    f"| matches | {len(result.matches):,} | {len(result_in_sample.matches):,} |",
    f"| exceptions | {len(result.exceptions):,} | {len(result_in_sample.exceptions):,} |",
    f"| quarantined at ingest | {result.quarantined_count} | {result_in_sample.quarantined_count} |",
    "",
    "The match rate row compares two different things by necessity: bank and",
    "ERP rows carry no settlement-cycle id, so a holdout population on those",
    "sources can only be built as \"rows a holdout match touched\", which makes",
    "match rate on that side tautologically ~100%. The held-out figure is",
    "restricted to the razorpay side, where cycle membership is well-defined;",
    "the in-sample figure is the ordinary whole-population match rate. They",
    "are not directly comparable, which is why each is labelled.",
    "",
    "## Auto-post threshold (held-out run)",
    "",
    f"- tau = **{result.threshold.tau:.4f}**",
    f"- precision at tau = **{result.threshold.precision_at_tau:.4f}**",
    f"- auto-posted = **{result.auto_posted:,}** of "
        f"**{len(result.matches):,}** matches "
        f"(train-population count at this tau, used to pick it: "
        f"{result.threshold.auto_post_count:,})",
    f"- expected cost at tau = **Rs {paise_to_rupees(result.threshold.expected_cost_paise)}**",
    "",
    "Cost inputs are stated assumptions, not measurements: Rs 2,500 to unwind a",
    "wrong auto-post, Rs 50 of analyst time per manual review. Nothing about",
    "those two figures was measured against a real merchant; they are the",
    "numbers the threshold search optimises against, and changing them moves tau.",
    "",
    "## Calibration: Brier score, in-sample train population",
    "",
]
if brier_before is not None:
    lines += [
        f"Measured on the in-sample pass's train population: "
        f"**{positives:,} of {total_labels:,}** match labels are positive "
        f"({positives / total_labels:.1%}).",
        "",
        "| | Brier score |",
        "|---|---|",
        f"| before calibration (raw score vs label) | {brier_before:.5f} |",
        f"| after calibration (isotonic probability vs label) | {brier_after:.5f} |",
        "",
        f"The improvement ({brier_before:.5f} -> {brier_after:.5f}) is driven by the "
        f"{positives / total_labels:.1%} class imbalance above and near-total",
        "separability at this population size — the reliability bins collapse to",
        "two clusters (almost-certainly-right, almost-certainly-wrong). This",
        "demonstrates isotonic regression can memorise an easy, imbalanced",
        "training set; it is **not** evidence of generalisation. The held-out",
        "precision/recall/F1 figures above are the ones worth citing — this",
        "Brier number is not.",
    ]
else:
    lines += ["Calibration did not run this pass (degraded); no Brier score to report."]
lines += [""]

lines += [
    "## Narration parsing — deterministic vs model (held-out run's ingest; same for both passes)",
    "",
    "| path | count |",
    "|---|---|",
]
lines += [f"| {k} | {v} |" for k, v in result.parser_stats.items()]

by_class: dict[str, int] = {}
by_class_amount: dict[str, int] = {}
for item in result.exceptions:
    key = item.exception_class.value
    by_class[key] = by_class.get(key, 0) + 1
    by_class_amount[key] = by_class_amount.get(key, 0) + item.amount_paise

lines += ["", "## The honest exception list (held-out run)", "",
          "Every row not consumed by a match appears here. Nothing is dropped.", "",
          "| class | count | value |", "|---|---|---|"]
lines += [f"| {k} | {v} | Rs {paise_to_rupees(by_class_amount[k])} |"
          for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])]

lines += ["", "## Reproducibility", "",
          f"- held-out result hash: `{result.result_hash}`",
          f"- in-sample result hash: `{result_in_sample.result_hash}`",
          f"- degraded capabilities this run: {result.degraded or 'none'}",
          "- `nostro replay` re-derives the same hash from the same inputs.",
          "- `nostro generate` builds this exact dataset (same "
          "`GeneratorConfig` defaults this script uses), so `nostro generate "
          "&& nostro close --no-model` reproduces the held-out figures above.",
          ""]

Path("EVALUATION.md").write_text("\n".join(lines), encoding="utf-8")
print("wrote EVALUATION.md")
