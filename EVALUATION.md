# Nostro — Measured Results

Every number below was produced by `python scripts/build_evaluation.py`.
None were typed by hand. Labels come from the generator that produced the data.

## Headline (held-out)

9 of 30 settlement cycles were withheld from the calibrator and the auto-post threshold entirely; these are the numbers measured on that unseen slice.

| metric | value |
|---|---|
| rows evaluated | 1,668 |
| razorpay-side holdout match rate | 0.9413 |
| precision | 0.9937 |
| recall | 0.6894 |
| F1 | 0.8140 |
| throughput | 351 rows/s |
| matches | 3,126 |
| exceptions | 541 |
| quarantined at ingest | 0 |

## Auto-post threshold

- tau = **0.9987**
- precision at tau = **0.9991**
- auto-posted = **3,114** of **3,126** matches (train-population count at this tau, used to pick it: 2,181)
- expected cost at tau = **Rs 5450.00**

Cost inputs are stated assumptions, not measurements: Rs 2,500 to unwind a
wrong auto-post, Rs 50 of analyst time per manual review. Nothing about
those two figures was measured against a real merchant; they are the
numbers the threshold search optimises against, and changing them moves tau.

## Narration parsing — deterministic vs model

| path | count |
|---|---|
| regex_hits | 1191 |
| llm_calls | 0 |
| misses | 137 |

## The honest exception list

Every row not consumed by a match appears here. Nothing is dropped.

| class | count | value |
|---|---|---|
| missing_counterparty | 478 | Rs 5485538.69 |
| unparseable_narration | 41 | Rs 188194.71 |
| duplicate_utr | 22 | Rs 339269.17 |

## Reproducibility

- result hash: `1dc43aeeef8a3375a8791d158eb4f4834f146f80ec861c3e66fc479502eac89b`
- degraded capabilities this run: ['llm']
- `nostro replay` re-derives the same hash from the same inputs.
