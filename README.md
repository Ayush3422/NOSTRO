# Nostro

Three-way settlement reconciliation for Indian merchants: Razorpay settlement report
against bank statement against ERP ledger, with measured accuracy and an honest
exception list — built for the Razorpay AI Buildathon (Track 4: AI Finance Controller).

## Measured results

Held-out: 9 of 30 settlement cycles were withheld from the calibrator and the
auto-post threshold entirely and evaluated only after fitting was done. In-sample
numbers are shown for contrast, not as the headline — they overstate what the
system does on data it has never seen. Every number in this section, and all of
`EVALUATION.md`, was produced by `python scripts/build_evaluation.py`; none were
typed by hand.

| metric | held-out | in-sample |
|---|---|---|
| precision | **0.9937** | 0.9905 |
| recall | **0.6894** | 0.6874 |
| F1 | **0.8140** | 0.8116 |
| match rate | **0.9413** (razorpay-side; see note below) | 0.9096 (whole population) |

The held-out match rate is scoped to the Razorpay side deliberately: bank and
ERP rows carry no settlement-cycle id, so the only way to build a holdout
population on those sources is "rows a holdout match touched" — which makes
match rate tautologically ~100% on that side and not comparable to an
in-sample figure. Razorpay-side match rate is the honestly-scoped alternative
and the one this project reports.

Dataset: 2,443 Razorpay rows + 1,328 bank rows + 2,211 ERP rows = **5,982 rows**,
3,522 ground-truth links, 411 genuine split settlements — regenerated
deterministically from a fixed seed. Razorpay's stated bar is 50 records.

**Auto-post threshold:** τ = **0.9987**, auto-posted **3,114 of 3,126** matches,
expected cost **Rs 5,450**. τ is chosen by minimising expected cost over
Rs 2,500 to unwind a wrong auto-post and Rs 50 of analyst time per manual
review — both are **stated assumptions, not measurements**; nothing about them
was benchmarked against a real merchant, and changing them moves τ.

**Narration parsing:** 1,191 of 1,328 bank narrations (89.7%) parse under a
deterministic regex ladder with no model attached; 137 (10.3%) don't and fall
through to the parser's `llm_fallback` injection point, which is unwired in
this build (see below) — those rows are simply left unparsed, not sent to a
live model.

**172 tests passing** (`python -m pytest -v`).

Full breakdown — exception list by class with rupee values, reproducibility
hash, degraded-capability list — is in [`EVALUATION.md`](./EVALUATION.md).

## Where we deliberately did not use AI, and why

The panel's "AI judgment" criterion is as much about restraint as capability.
Three decisions in this build use no model at all, on purpose:

- **Matching is a bounded subset-sum solver, not a model.** A settlement credit
  either sums to its constituent payments within a fixed paise tolerance, or it
  doesn't — that is arithmetic, not judgment, and arithmetic must be
  reproducible and auditable. `nostro replay` re-derives the identical result
  hash from the identical inputs; a model in that path would make that
  impossible. `src/nostro/match/solver.py` grounds this out: subsets are
  constrained to one settlement cycle, matched against a near-exact sum, and
  the search is depth-first with a measured bound (`max_candidates=15`, chosen
  from the sweep below).
- **Auto-post gating is a deterministic cost-minimising threshold, not a
  model.** `src/nostro/policy/gate.py` sweeps candidate τ values and picks the
  one with the lowest expected cost under the stated Rs 2,500 / Rs 50 cost
  model. A model does not get to own a money-movement decision in this build —
  the threshold is a number a CFO can audit and argue with, not a confidence
  score to trust or not.
- **Exception classification is a rule tree, not a model.** By the time a row
  reaches `src/nostro/exceptions/taxonomy.py` its inputs — matched or not,
  narration parsed or not, UTR duplicated or not — are already structured. A
  model adds latency and non-determinism to a decision a handful of `if`
  statements already makes correctly.

The model is wired in exactly **one** place in this build, downstream of every
number above: drafting a proposed resolution for a human to approve at the
exception desk (`src/nostro/exceptions/agent.py` — `requires_human` is
hard-coded `True` on every proposal; the desk cannot post, move money, or
change a classification). Turn the model off entirely (`use_model=False` /
`nostro close --no-model`) and precision, recall, and the exception count do
not move. `tests/chaos/test_chaos.py::test_model_outage_still_closes_the_books`
asserts exactly that, byte-for-byte, with the model dead mid-close.

`NarrationParser` also exposes an `llm_fallback` injection point for the
~10% of bank narrations the regex ladder can't parse, and the chaos suite
exercises it against a stub to prove the seam behaves under model outage
and schema drift. Nothing in this build's production code path
(`pipeline.py`, `api/main.py`, `normalize/canonical.py`, `scripts/`) wires
that parameter to a live model — narration parsing today is regex-only.
The seam is deliberate design-for-it, not a claim that it's active.

One more deliberate non-choice: the spec named the Claude Agent SDK. We used
the plain Anthropic SDK's structured outputs (`client.messages.parse`) instead,
because the Agent SDK is Claude Code packaged as a library — filesystem and
bash tools aimed at coding agents — and the exception desk needed a narrow,
propose-only surface over data already in hand, not an open-ended tool loop.

## Architecture

```
ingest -> normalise -> block -> deterministic -> subset-sum
       -> score -> calibrate -> gate -> exceptions -> evaluate
```

- **ingest** — load the three CSVs; malformed rows are quarantined, never dropped.
- **normalise** — map each source's schema onto one `CanonicalRow`; bank narrations run through the regex/LLM parser here.
- **block** — cut the O(n²) comparison space down with cheap keys (date, amount range) before any real matching runs.
- **deterministic** — exact 1:1 matches on UTR/amount, one axis at a time (Razorpay↔bank, Razorpay↔ERP), each with its own consumed-row set.
- **subset-sum** — a bounded solver recovers split settlements: one bank credit against several Razorpay payments in the same cycle.
- **score** — turn each match's features into a raw score.
- **calibrate** — an isotonic regressor turns raw scores into probabilities, fit on train-cycle labels only.
- **gate** — a cost-minimising threshold τ splits matches into auto-post vs. review.
- **exceptions** — every unmatched or quarantined row is classified by a rule tree; the model may draft a proposed resolution here, always requiring human sign-off.
- **evaluate** — precision, recall, F1, match rate, all measured against held-out truth when a holdout split is configured.

Everything up to `exceptions` is deterministic; the model, when present, never
touches a number that was already computed.

## What broke, and how we got out

**The matcher was silently doing two disjoint two-way reconciliations, not a
three-way one.** Early on, `match_deterministic` tracked a single global
`consumed` set shared across both axes. The Razorpay↔ERP pass ran first and
claimed rows before the bank-axis matcher or the split-solver ever got to see
them — so the solver was starving on its own dataset. It surfaced because
adding the subset-sum solver moved recall by *exactly zero*: 0.5059 before and
0.5059 after, to four decimal places. Diagnosis came from counting ground-truth
pairs by axis (2,211 ERP-side links vs. 2,361 bank-side links) and noticing the
measured recall almost exactly matched the ERP-only share — the bank axis was
being starved out of the result entirely. The fix: consumption became
per-axis, because a single payment legitimately participates in two matches at
once (one with its invoice, one with its bank credit), and a global consumed
set was enforcing an invariant that was never true. Recall moved
**0.5059 → 0.6754** on the deterministic pass alone, before the solver added
anything further.

**We nearly published a fabricated finding.** The first held-out threshold
search came back with τ = 1.0 — "never auto-post" — which reads like a
considered, conservative risk call. It wasn't; it was a bug. The calibrator was
scored against the *full* match population but labelled from *train-only*
truth, so every holdout-cycle match — including genuinely correct ones — was
forced to label 0, because its truth link could never appear in train-only
data by construction. That manufactured several hundred false negatives inside
the cost sweep and pushed τ to the ceiling. Fixing the population mismatch
(predictions and labels drawn from the same cycle population, on both sides of
the train/holdout split) moved τ from 1.0 to 0.9984 and auto-posting from 0 to
2,515 matches on that run. The lesson is now pinned by tests on both the
train-fitting side and the holdout-evaluation side
(`tests/test_pipeline.py::test_calibrator_never_sees_holdout_labels`,
`test_tau_uses_only_train_cycle_predictions`).

Chaos suite, run against a smaller 20-cycle / 40-payment-per-cycle dataset for
speed (`scripts/chaos_report.py`), reflects the system under active stress
rather than an idealised close. Its metrics are measured **in-sample**, not
held-out — that is why its baseline recall (79.74%) sits well above this
README's held-out headline (68.94%) rather than lining up with it; the two
are different measurement bases, not a contradiction:

| scenario | match rate (in-sample) | precision (in-sample) | recall (in-sample) | exceptions |
|---|---|---|---|---|
| baseline | 93.61% | 96.36% | 79.74% | 138 |
| narration destroyed | 93.57% | 99.62% | 81.00% | 138 |
| heavy splits | 87.26% | 98.69% | 55.67% | 247 |
| duplicate UTRs | 93.47% | 97.99% | 80.70% | 141 |
| everything at once | 87.41% | 97.20% | 57.57% | 250 |

Precision holds up under every scenario — the system does not guess when it
isn't sure, it escalates. Recall is the number that moves, most under "heavy
splits", which is exactly the solver's bounded-search limitation showing up
under load (see Limitations).

## How to run

```bash
pip install -e .
nostro generate                 # writes data/full + the holdout split
nostro close --no-model         # or drop --no-model to use the exception desk
nostro verify                   # confirms the audit ledger is unbroken
nostro replay                   # confirms the close reproduces byte-for-byte
```

`nostro generate` and `python scripts/build_evaluation.py` build from the same
`GeneratorConfig` defaults, so `nostro close` reproduces the exact headline
numbers in this README — verified by running both and comparing result hashes.

Alternatively:

```bash
docker compose up
# api on http://localhost:8000, ui on http://localhost:3000
```

## Honest limitations

1. **Cost assumptions are stated, not measured.** Rs 2,500 to unwind a wrong
   auto-post and Rs 50 of analyst time per review drive τ and the expected-cost
   figure everywhere they appear in this README and in `EVALUATION.md`. They
   were not benchmarked against a real merchant's actual costs.
2. **The audit hash chain is unsigned.** It detects edits, mid-ledger deletion,
   and reordering, but **not tail truncation or a forged append** — an
   attacker who controls the file can drop the last N entries and the chain
   still verifies. This is pinned by
   `tests/audit/test_ledger.py::test_tail_truncation_is_NOT_detected_unsigned_chain_limitation`,
   deliberately, so that a future "fix" for this without adding real signing
   doesn't get merged as if the gap were closed. A production deployment needs
   either signing or an external checkpoint outside the file itself.
3. **The in-sample calibration improvement is not evidence of generalisation.**
   `EVALUATION.md`'s "Calibration" section (generated by the same script, not
   hand-typed) shows the Brier score moving from 0.11829 to 0.00096 in-sample,
   but that is driven by **99.6% class imbalance** (3,112 of 3,126 labels
   positive) and near-total separability, where the reliability bins collapse
   to two. It demonstrates that isotonic regression can memorise an easy,
   imbalanced training set — it is not proof the calibration generalises. The
   held-out precision/recall/F1 figures above are the ones worth citing; the
   in-sample Brier number is not.
4. **The solver is a bounded search, not a complete one.** Subsets are
   constrained to a single settlement cycle and to a near-exact sum, and
   `max_candidates` is a measured tractability bound, not a theoretical limit.
   At the shipped bound of 15, the solver recovers **24 of 411** genuine split
   settlements. Looser bounds recover more but cost more and let precision
   fall as coincidental near-sum matches get admitted. The table below is a
   **historical, one-off sweep** used at the time to choose `max_candidates`
   — it is **not** regenerated by `scripts/build_evaluation.py`, and its
   metrics are measured **in-sample** (not held-out), so its `n=15` row
   (recall 0.6874) lines up with this README's in-sample recall above, not
   the held-out headline (0.6894). Two different recall figures in this
   document that don't match are not a mistake; they're two different
   measurement bases, each labelled:

   | max_candidates | wall-clock (s) | precision (in-sample) | recall (in-sample) | F1 (in-sample) | splits recovered (of 411) |
   |---|---|---|---|---|---|
   | 12 | 0.33 | 0.9949 | 0.6824 | 0.8095 | 14 |
   | **15 (shipped)** | 0.54 | 0.9905 | 0.6874 | 0.8116 | 24 |
   | 20 | 2.00 | 0.9444 | 0.6982 | 0.8028 | 40 |
   | 25 | 7.21 | 0.8774 | 0.7108 | 0.7854 | 50 |

   15 was chosen on measured best F1, not on recall alone — a looser bound
   trades precision for split recovery, and this sweep is the record of what
   that trade actually costs.
5. **Synthetic data only.** No real Razorpay API calls are made anywhere in
   this build. The dataset regenerates deterministically from a fixed seed
   (`GeneratorConfig`); `data/` is gitignored and is never committed.
6. Single-merchant, single-currency. No multi-tenant, multi-currency, or
   partial-refund-of-a-refund handling exists.
