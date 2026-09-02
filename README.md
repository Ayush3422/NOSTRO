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

**173 tests passing** (`python -m pytest -v`).

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

## Build decisions log

This project was built by a controller dispatching fresh implementer and
reviewer subagents per task, with a written ruling every time the plan
conflicted with what a reviewer found, what real data showed, or what the
spec actually required. **51 rulings**, numbered in the order they were made.
Every one is listed below — including the two whose detail didn't survive the
session, named honestly rather than reconstructed from a guess.

Read the "cost if wrong" column before the "decision" column if you're
deciding whether to trust any of this: almost every ruling here exists
because a number was real but not comparable, a claim was aspirational
stated as fact, or a threshold looked like a judgment but was an artifact.
The code was mostly fine. What needed policing was whether it told the
truth about itself — which is also, not coincidentally, this project's
whole pitch.

<details>
<summary><strong>Setup — before any code existed (R0–R0b)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R0 | — | Whether to implement on `main` or an isolated branch. | Work on `build/nostro`. Implementing on a default branch without consent is against process, and the repo was brand new so isolation cost nothing. | One branch rename before push. |
| R0b | — | Installed Python is 3.12.10; the plan's floor is `>=3.11`. | Build on 3.12, leave the floor at 3.11 — it's satisfied. | None — a floor is a minimum, not a target. |

</details>

<details>
<summary><strong>Pre-flight scan — read before Task 1 was dispatched (R1–R4)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R1 | 10 | The solver's plan text assigns `by_id = blocks.row_index` and never reads it. | Dead local — instructed the implementer to omit it before writing a line of code. | None. |
| **R2** | 15 | The plan fits the calibrator and evaluates on *every* ground-truth link — leakage, and it contradicts the spec's binding held-out-by-cycle requirement. | Split links into train/holdout by settlement cycle; fit the calibrator and choose τ on train only; report on holdout. | Optimistic precision/recall — the exact dishonesty this project exists to beat. (Later found to be under-specified itself — see R28/R29.) |
| R3 | 8 | Blocking sweeps ~1,400 dict lookups per row, ~7M for the full dataset. | Accepted as written — correctness first, throughput is a reported metric, not a target. | A slower throughput number in the write-up, nothing more. |
| R4 | 18 | A close whose live model client raises doesn't record `"llm"` in `degraded` — only `use_model=False` does. | Deferred to the final review; the chaos suite's real claim is number-identity, not the flag. | UI under-reports one degradation mode. (Closed properly at R47.) |

</details>

<details>
<summary><strong>Foundation — generator, ingest, normalisation, blocking, Tasks 1–9 (R5–R16)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R5 | 4 | A `merged_credit_rate` chaos knob was declared but never implemented, while the module claimed "ten injectors." | Removed rather than implemented — a knob that silently does nothing is precisely the dishonesty this project's README argues against. | Lose an injector never had. |
| R6 | 4 | All seven generator tests checked ground-truth *shape*, never *values* — a wrong-subset bug would have passed silently. | Added a test asserting a linked bank credit equals the exact rupee sum of its linked payments, over a genuine multi-payment split. | One extra test to maintain. |
| **R7** | 5 | A shared helper zeroed blank amounts on every source; blank is legitimate on bank debit/credit but not on Razorpay or ERP core amounts. | Split into a blank-tolerant path (bank only) and a strict path (Razorpay/ERP); verified real refunds carry `"0.00"`, never blank. | A legitimately-blank amount would quarantine — visible, not silent. |
| R8 | 5 | Blank `payment_id`/`order_id`/`invoice_no` passed validation silently; the bank contract already blocked this, the other two didn't. | Added the same blank-check validator to all three contracts. | One extra validator per contract. |
| R9 | 6 | The squeeze-copy narration retry used a `\b`-anchored pattern against a string with every non-alphanumeric character stripped — no internal `\b` positions exist, so it only ever matched the degenerate case. | Rewrote with a digits-only pattern, verified against the generator's real UTR format, and re-measured the miss rate the README publishes. | A pattern too narrow for a UTR containing letters — visible as a higher miss rate, not a silent wrong answer. |
| R10 | 6 | A dead `+= 0` line and an unused wrapper function sat in the module that is the project's primary "didn't use AI" exhibit. | Routed in despite being Minor — dead code in the exhibit undermines the exhibit. | Negligible. |
| R11 | 7 | `CanonicalSet.by_id()` silently dropped a row on a cross-source id collision; the reviewer's proposed fix (namespace the keys) would have broken four downstream consumers that address rows by bare id. | Overrode the reviewer — raise loudly on collision instead, preserving the bare-id contract everything else depends on. | A genuine collision aborts the close instead of silently losing a row — the correct failure direction for a books system. |
| R12 | 8 | Pass 1 (reference identity) had no direction filter. Confirmed live: refunds and chargebacks inherit the original payment's `order_id`, and `cb_` sorts before `pay_`, so a chargeback debit could claim the ERP invoice the real payment needed. | Added the same direction filter passes 2–3 already had. | None — only removes pairings that must never happen. |
| R13 | 8 | Pass 1 labelled every order-id match `EXACT` even when Razorpay-net vs. ERP-gross legitimately differ by fee+GST, making the published "exact" count uninterpretable. | Added a new `REFERENCE` match type, ranked equal to `EXACT` so scoring's normalisation stays untouched. | One extra enum member; metrics get more granular, not less honest. |
| R14 | 8 | A dead config field, same defect class as R5. | Removed for consistency. | None. |
| R15 | 8/9 | *Not recovered — see note at the end of this log.* | | |
| R16 | 9 | `filter_to_cycles` — the mechanism the entire held-out claim rests on — shipped with no direct unit test. | Carried the requirement forward into Task 15 rather than reopening an already-clean task. | Coverage landed one task later than ideal. |

</details>

<details>
<summary><strong>Matching &amp; the subset-sum solver — Task 10, four fix rounds, the hardest task in the build (R17–R21)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| **R17** | 10 | A single global "consumed" set meant pass 1 claimed ~2,211 rows before the bank-axis matcher or solver ever ran. Confirmed by counting ground-truth pairs by axis (2,211 ERP-side, 2,361 bank-side) — measured recall (0.5059) matched the ERP share almost exactly. | Consumption became per-axis: a payment legitimately matches once on each axis, exactly as the ground truth encodes it. | If the axes weren't truly independent, precision would fall — visible immediately, not silent. |
| R18 | 10 | Once the bank axis opened, the solver produced 49 false positives per genuine match. Measured: all 411 genuine splits share one settlement cycle *and* sum exactly to the credit — 411/411 on both. | Constrained subsets to one cycle and a near-zero residual — both domain-real, not fitted to the generator. | A real multi-cycle settlement or >2-paise drift would be missed and reported as an exception — the correct failure direction. |
| R19 | 10 | `max_candidates=40`, written before the cycle constraint existed, made the solver stage project to ~28 minutes. | Swept {12,15,20,25}, adopted 15 on best F1, shipped the full curve instead of picking a number by feel. | A tighter bound recovers fewer splits — visible in the sweep table, not hidden. |
| R20 | 10 | The 2-paise tolerance tightening had no integration-level test — every fixture summed to residual 0, so nothing distinguished the new bound from the old 100-paise one. | Added a 50-paise-off subset (refused) and a 1-paise boundary case (accepted). | None. |
| R21 | 10 | Blank `settlement_cycle` had no validator, unlike the identifiers fixed at R8 — a blank cycle would pool unrelated batches under one key and defeat R18 entirely. | Added the same blank-check validator for consistency. | A row with a genuinely absent cycle quarantines instead of silently mis-grouping. |

</details>

<details>
<summary><strong>Scoring, calibration, policy gate — Tasks 11–12 (R22–R25)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R22 | 11 | `raw_score` divided by `1 + residual/10`, exactly zero at `residual = -10`; the "absurd input" test only used a large positive residual, so the crash never surfaced. | Guarded the term against a negative residual; added the missing test. | None. |
| R23 | 11 | A docstring asserted "every reported number comes from the held-out cycles" as a present-tense guarantee the code didn't enforce. | Reworded to describe the caller's obligation, not a guarantee. | Negligible. |
| R24 | 12 | τ candidates were built from *rounded* probabilities but compared against *unrounded* ones; float rounding isn't monotone in the needed direction, so the true cost-optimum could be skipped. | Dedupe candidates on raw floats, no rounding. | None — strictly widens the candidate set. |
| R25 | 12 | The gate's docstring claimed "the README labels them as such" before the README said any such thing. | Reworded to state the requirement; carried the actual obligation forward to Task 19. | Negligible. |

</details>

<details>
<summary><strong>Audit ledger &amp; exception desk — Tasks 13–14 (R26–R27)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R26 | 13 | The unsigned hash chain can't detect tail truncation or a forged append — the most attacker-relevant case — while the docstring claimed a blanket "detects edits and deletions." | Did not attempt signing (real scope, out of a 3-day build). Scoped the docstring to the true guarantee and **added a test that pins the limitation by name.** | None — ships an accurate claim instead of an inflated one. |
| **R27** | 14 | **Critical.** `propose()` read a model attribute outside its own try/except. The Anthropic SDK returns a null parsed-output *without raising* on truncation, refusal, or schema-invalid content — so a real truncated response would crash the desk instead of degrading, breaking the exact claim the chaos suite exists to prove. | Guarded for the null case, routed to `NEEDS_HUMAN`; added a stub reproducing the SDK's real failure shape. | None — a pure correctness fix on the project's central claim. |

</details>

<details>
<summary><strong>The held-out split — where the honest numbers came from, Tasks 15–16 (R28–R34)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| **R28** | 15 | R2 (above) was under-specified — it said "report on holdout" but never restricted the *predicted* set to holdout too. The evaluation compared the full match list against holdout-only truth, so ~90% of matches (train-cycle ones) counted as false positives. Precision came out 0.2972 against an in-sample 0.9887. | Filtered predictions to the holdout population before evaluating, on both sides of the comparison. | Either absurdly pessimistic or silently optimistic — both fatal for a project selling honest metrics. |
| **R29** | 15 | **Critical.** The same mismatch on the *train* side: the calibrator and τ search saw the full match list while labels came from train-only truth, forcing every holdout-cycle match — including correct ones — to label 0. This produced τ=1.0, "never auto-post," which the first draft wrongly explained as a considered risk judgment rather than an artifact. | Restricted matches, not just labels, to train-cycle predictions before fitting or choosing τ. | We would have shipped a calibrator trained on ~30% garbage labels *and* a fabricated finding presented as risk analysis — worse than a bad number. |
| R30 | 15 | The held-out match rate was inflated by construction — the restricted population only contained bank/ERP rows a match already touched, so numerator equalled denominator tautologically on those two sources. | Reported the held-out match rate over the Razorpay side only, where cycle membership is well-defined, and named it accordingly. | The metric became narrower in scope but honestly labelled instead of quietly overstated. |
| R31 | 16 | The plan's own API code read the biased whole-population field directly — the very next task would have silently reintroduced R30's disowned metric into the API and the dashboard. | Required the API to surface the honestly-scoped field in holdout mode, never the raw biased one. | Dashboard and any judge reading the API would see an overstated match rate. |
| R32 | 16 | The drill-down endpoint dumped a match's fields directly; the calibrated probability defaults to zero and is only populated in a parallel list one endpoint re-injected and the other didn't — so every drill-down showed a fabricated 0.0 confidence on the exact screen meant to prove the audit trail. | Injected the aligned probability the same way the working endpoint does, pinned with a cross-endpoint agreement test. | The demo's proof-of-audit screen displays a fabricated confidence. |
| R33 | 16 | The audit ledger recorded the disowned biased match rate even after the API stopped surfacing it — writing a number already disowned into the permanent record. | The ledger entry now records the honestly-scoped rate and labels which evaluation mode produced it. | None — the audit trail becomes more precise. |
| R34 | 16/17 | *Not recovered — see note at the end of this log.* | | |

</details>

<details>
<summary><strong>Chaos suite — Task 18 (R35–R36)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R35 | 18 | A rewritten duplicate-UTR test tracked overlap on the wrong two id sets — the actual threat (one Razorpay row claimed by two *different* bank rows sharing a UTR) produced no overlap on the sets being checked, and passed silently. | Unioned the shared id into each axis's seen-set, checked independently; verified by constructing the exact failure and watching the assertion fire. | None. |
| R36 | 18 | The new degraded-flag fix had no negative test — nothing asserted the flag was *absent* on a healthy run. | Added a healthy-client test asserting absence. | None. |

</details>

<details>
<summary><strong>Submission artifacts — Task 19 (R37–R40)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| R37 | 19 | **Reproducibility bug.** The CLI's default row volume didn't match the generator config's default — the CLI and the evaluation script produced *different datasets* and different headline numbers. The implementer correctly refused to hand-type the controller's supplied figures over their own real generated output. | Made the CLI read its default from the one config the evaluation script also uses; verified an identical result hash between the two entry points. | None — removes a fork rather than adding one. |
| R38 | 19 | Docker couldn't be run in this environment (no daemon available). | The README's "how to run" leads with the verified CLI path; Docker is documented as an alternative, never claimed as run. | None — an honesty/ordering decision only. |
| **R39** | 19 | **Critical.** The README stated "none were typed by hand" directly above a results table whose in-sample column and calibration figures *were* hand-copied from earlier drafts — possibly from a different dataset generation entirely. | Made the claim true instead of softening it: extended the evaluation script to run both passes and compute the calibration figures live, then regenerated. | None — one extra pipeline run per evaluation build. |
| R40 | 19 | A solver sweep table sat unlabelled beside the headline numbers, with one of its rows not matching the current headline figure and no explanation given. | Labelled it as a historical bound-selection artifact, stated when and how it was run, said plainly it isn't regenerated by the evaluation script. | None. |

</details>

<details open>
<summary><strong>Final whole-branch review — the last gate before submission (R41–R49)</strong></summary>

| Id | Task | Finding | Decision | Cost if wrong |
|---|---|---|---|---|
| **R41** | final | **Critical.** The README claimed the model is used "in exactly two places"; a repo-wide search confirmed the narration fallback is never wired in any production path — only one place (the exception desk) is real. | Corrected the claim to one place; described the narration seam as exposed-but-unwired rather than implementing a live path under deadline pressure. | None — makes the claim match the code. |
| R42 | final | `nostro replay` failed on the README's own documented flow — the close appended to the ledger instead of starting fresh, so two closes (or a model / no-model pair) produced different hashes. | Every close now starts from a fresh ledger file, mirroring what replay already did for its own scratch copy. | An older audit history is discarded per close — correct behaviour for "this close's ledger." |
| R43 | final | Docker Compose pointed the UI container at the API via `localhost`, which inside a container is the UI container itself. | Fixed to address the API by its service name. | None. |
| R44 | final | Held-out throughput divided a holdout-restricted row count by the full close's wall-clock time, understating it roughly 3.6× and making it incomparable to the figure shown beside it. | Report throughput once, over the full batch, labelled accordingly. | None — one fewer misleading number. |
| R45 | final | The drill-down screen showed only the first of a row's two matches — on the one screen meant to prove three-way reconciliation, it showed two-way. | Return every match touching a row, render each with its own probability. | Completed rather than deferred, despite being explicitly optional under time pressure. |
| R46 | final | The exception list is exhaustive over the union of both matching axes, so a row resolved on one axis but not the other isn't listed even though its reconciliation is incomplete — making the count look inconsistent with recall. | Added one clarifying sentence to the evaluation write-up rather than building a new exception class under deadline. | None — a clarifying sentence only. |
| **R47** | final | **Correctness.** The auto-post gate fails open under a degraded calibrator: raw scores can hit exactly the ceiling, and the decision rule is inclusive at the boundary, so a pass-through calibrator could auto-post on zero evidence with nothing in the degradation report saying so. | Detect a genuine pass-through directly; force the threshold to post nothing and record the degradation when it fires. | A degraded run posts nothing automatically instead of everything — the safe failure direction for a books system. |
| R48 | final | Ledger verification crashed with a raw traceback on a corrupted line instead of reporting the break — the single most likely way anyone actually pokes at tamper-evidence live. | Catch the parse failure per line, report it the same way a broken hash chain is already reported. | None. |
| R49 | final | The test cited *by name* in the README as proof of the R29 fix still only asserted a trivial bound — it would not have caught the bug it's named for. | Strengthened the assertion to check the actual signature of the fixed bug. | None. |

</details>

**On R15 and R34.** Two ruling numbers exist in the sequence with no
recoverable detail. The live ledger these were written to lived outside the
git repository by design — it was scratch space, cleaned up at the end of
the build per the standard process for finishing a development branch. That
cleanup should have happened *after* every ruling was extracted into a
permanent record; here it happened one step too early. Reconstructing
precise wording for those two from memory risked misattributing a decision
or its reasoning — exactly the failure mode this log exists to prevent — so
they're marked as gaps instead of guesses. The corresponding code changes
are real and are in the commit history; only this log's account of *why* is
incomplete for those two.
