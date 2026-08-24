# Nostro — Measured Results

Every number below was produced by `python scripts/build_evaluation.py`.
None were typed by hand. Labels come from the generator that produced the data.

## Dataset

2,443 Razorpay rows + 1,328 bank rows + 2,211 ERP rows = **5,982 rows**, 3,522 ground-truth links, 411 genuine split settlements (a link with more than one Razorpay leg) — regenerated deterministically from a fixed seed. Razorpay's stated bar is 50 records.

## Headline: held-out vs in-sample

9 of 30 settlement cycles were withheld from the calibrator and the
auto-post threshold entirely for the held-out pass; the in-sample pass
re-runs the identical close with `holdout_cycles=()`, so calibration,
gating, and evaluation all see every link. Both passes are the same
dataset generation and the same code path — only the split differs.
The held-out column is the one worth citing; in-sample is shown to make
the honest gap between them visible, not to headline it.

| metric | held-out | in-sample |
|---|---|---|
| rows evaluated | 1,668 | 5,982 |
| match rate | 0.9413 (razorpay-side) | 0.9096 (whole population) |
| precision | 0.9937 | 0.9905 |
| recall | 0.6894 | 0.6874 |
| F1 | 0.8140 | 0.8116 |
| throughput | 347 rows/s | 1,262 rows/s |
| matches | 3,126 | 3,126 |
| exceptions | 541 | 541 |
| quarantined at ingest | 0 | 0 |

The match rate row compares two different things by necessity: bank and
ERP rows carry no settlement-cycle id, so a holdout population on those
sources can only be built as "rows a holdout match touched", which makes
match rate on that side tautologically ~100%. The held-out figure is
restricted to the razorpay side, where cycle membership is well-defined;
the in-sample figure is the ordinary whole-population match rate. They
are not directly comparable, which is why each is labelled.

## Auto-post threshold (held-out run)

- tau = **0.9987**
- precision at tau = **0.9991**
- auto-posted = **3,114** of **3,126** matches (train-population count at this tau, used to pick it: 2,181)
- expected cost at tau = **Rs 5450.00**

Cost inputs are stated assumptions, not measurements: Rs 2,500 to unwind a
wrong auto-post, Rs 50 of analyst time per manual review. Nothing about
those two figures was measured against a real merchant; they are the
numbers the threshold search optimises against, and changing them moves tau.

## Calibration: Brier score, in-sample train population

Measured on the in-sample pass's train population: **3,112 of 3,126** match labels are positive (99.6%).

| | Brier score |
|---|---|
| before calibration (raw score vs label) | 0.11829 |
| after calibration (isotonic probability vs label) | 0.00096 |

The improvement (0.11829 -> 0.00096) is driven by the 99.6% class imbalance above and near-total
separability at this population size — the reliability bins collapse to
two clusters (almost-certainly-right, almost-certainly-wrong). This
demonstrates isotonic regression can memorise an easy, imbalanced
training set; it is **not** evidence of generalisation. The held-out
precision/recall/F1 figures above are the ones worth citing — this
Brier number is not.

## Narration parsing — deterministic vs model (held-out run's ingest; same for both passes)

| path | count |
|---|---|
| regex_hits | 1191 |
| llm_calls | 0 |
| misses | 137 |

## The honest exception list (held-out run)

Every row not consumed by a match appears here. Nothing is dropped.

| class | count | value |
|---|---|---|
| missing_counterparty | 478 | Rs 5485538.69 |
| unparseable_narration | 41 | Rs 188194.71 |
| duplicate_utr | 22 | Rs 339269.17 |

## Reproducibility

- held-out result hash: `1dc43aeeef8a3375a8791d158eb4f4834f146f80ec861c3e66fc479502eac89b`
- in-sample result hash: `ebf32a285dbb6aa1654e47315c74cf49b16387287572391c933d87d07763c67d`
- degraded capabilities this run: ['llm']
- `nostro replay` re-derives the same hash from the same inputs.
- `nostro generate` builds this exact dataset (same `GeneratorConfig` defaults this script uses), so `nostro generate && nostro close --no-model` reproduces the held-out figures above.
