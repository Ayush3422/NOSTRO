# Nostro — Design Spec

**Razorpay AI Buildathon 2026 · Track 04: AI Finance Controller**
Date: 2026-08-23 · Submission deadline: 2026-09-05 · Build window: 3 days

---

## 1. The problem

An Indian merchant on Razorpay receives one net NEFT credit per settlement cycle.
That single bank line is the sum of many payments, minus platform fees, minus GST on
those fees, minus refunds, minus chargeback debits — and the components do not all
belong to the same day. Finance teams reconcile this by hand in spreadsheets, and
report spending 30–50% of month-end on reconciliation and exception chasing.

The loop Nostro closes: **three-way settlement reconciliation.**

```
Razorpay settlement report  ─┐
Bank statement (narration)  ─┼─→  matched set + match rate + honest exception list
ERP / sales ledger          ─┘
```

## 2. Track bar, and how we clear it

Razorpay's stated bar for Track 04:

> Build an agent that closes one finance-ops loop across a 50+ record batch of
> synthetic data, reporting its match rate and the exceptions it could not resolve.
> Throughput plus measured accuracy plus an honest exception list. One cherry-picked
> match proves nothing.

| Bar | Nostro's answer |
|---|---|
| 50+ record batch | 5,000+ records, held-out split, from an adversarial generator |
| Match rate | Reported per source pair and overall, with N:M splits counted honestly |
| Measured accuracy | Precision / recall / F1 against generator-owned ground-truth labels |
| Honest exception list | Typed taxonomy, every unresolved item enumerated, nothing hidden |
| Throughput | Records/sec on the full batch, reported |

## 3. Competitive positioning

Public repos self-identifying with each track (created since 2026-08-05, measured
2026-08-23): Revenue Recovery 44, Agentic Commerce 30, Risk Manager 21,
**Finance Controller 15**. Track 04 is the thinnest field.

Razorpay Agent Studio already ships *Settlement Insights* and *Cashflow Forecaster*
(built on Anthropic's Claude Agent SDK). Building either invites comparison with
their own shipped product. Multi-source reconciliation with exception resolution is
the direction they have **not** productised — and the one competing student repos
consistently get wrong.

Observed failure modes in the competing field, which Nostro is designed to beat:

1. The LLM performs the matching → non-deterministic, unauditable, does not scale.
2. Exact-amount 1:1 matching only → collapses on the first split settlement.
3. No ground truth → "match rate" is asserted, never measured.
4. No exception taxonomy → unmatched rows are dumped in a table.
5. No calibration → no defensible answer to "when is it safe to auto-post?"

## 4. Core thesis

**The LLM never does arithmetic.**

Matching is a solver. Gating is deterministic policy. AI is confined to the parts
that are genuinely fuzzy: parsing garbage bank narration when the regex ladder
misses, classifying an exception, and drafting a proposed resolution. The README
carries a section titled *"Where we deliberately did not use AI, and why"* — this
answers the panel's **AI judgment** criterion structurally rather than rhetorically.

## 5. Architecture

```
ingest/       pydantic contracts per source; schema-drift guard; quarantine bucket
              sources: razorpay_settlement.csv, bank_statement.csv, erp_sales.csv

normalize/    canonical rows; amounts as integer paise (never float)
              narration parser: deterministic regex ladder FIRST, LLM only on miss

match/        A. blocking      - date window +/-3d, amount band, UTR/RRN exact
              B. deterministic - 1:1 exact and tolerance matches
              C. N:M solver    - subset-sum DP over candidate pool
                                 + min-cost assignment (scipy linear_sum_assignment)
              every match scored -> isotonic-calibrated probability

policy/       auto-post iff p >= tau, tau derived from the rupee-cost curve
              below tau -> exception queue. Deterministic. No LLM on this path.

exceptions/   Claude Agent SDK agent: classify to taxonomy, propose typed resolution
              read-only tools + propose_journal_entry(); execution requires policy gate

audit/        append-only hash-chained JSONL; every UI number drills to source rows
              replay CLI re-runs the entire close deterministically from the log

eval/         held-out run -> match rate, P/R/F1, calibration curve, exception list

api/          FastAPI
ui/           Next.js - close summary, exception desk, drill-down
chaos/        LLM outage, schema drift, corrupt file, duplicate UTRs
```

## 6. Data model

Canonical row (all sources normalise to this):

| Field | Type | Note |
|---|---|---|
| `source` | enum | razorpay / bank / erp |
| `row_id` | str | stable, source-scoped |
| `amount_paise` | int | integer only; float arithmetic is banned repo-wide |
| `direction` | enum | credit / debit |
| `value_date` | date | |
| `refs` | dict | utr, rrn, payment_id, order_id, invoice_no — any subset |
| `narration_raw` | str | bank only |
| `parsed_by` | enum | regex / llm / none — provenance for the parser fallback |

A **match** is `(set[razorpay_row], set[bank_row], set[erp_row], score, p, method)`
where `method` is one of `exact`, `tolerance`, `subset_sum`, `assignment`.

## 7. The adversarial generator

The highest-ROI component. Because it emits the labels, every metric downstream is
real rather than asserted. Chaos injectors, each independently toggleable:

- Paise-level rounding drift on fee + GST computation
- T+2 rolling settlement, with components spanning cycle boundaries
- Split settlements (one credit to many payments) and merged credits
- Duplicate and near-duplicate UTRs
- Mis-keyed / truncated / bank-specific narration formats
- Refunds netted into a later cycle rather than debited
- Chargeback debits appearing without a matching forward entry
- Late credits arriving out of order
- Missing rows on one side entirely (true unmatchables — the honest exception floor)

Ground truth is the injector's own record of what it linked. The held-out split is by
settlement cycle, not by row, to prevent leakage.

## 8. Metrics reported

- Match rate, overall and per source pair
- Precision / recall / F1 on the held-out split
- Calibration: reliability curve + Brier score
- **tau selection**: the rupee cost of one wrong auto-post vs. the analyst-minutes
  cost of one unnecessary exception, plotted; tau is chosen on that curve and defended
- Precision at tau, and the volume auto-posted at tau
- Throughput: records/sec on the full batch
- Exception list: every unresolved item, by taxonomy class, with counts

## 9. Failure recovery

This feeds the form's final question — *"what broke, and how you got out"* — which
Razorpay states they read first. Each is an executable chaos test, not a claim:

| Injected failure | Designed behaviour |
|---|---|
| LLM unavailable / times out | Regex ladder + solver still close the books; automation rate drops, correctness does not; degradation is logged and surfaced in the UI |
| Source schema drifts | Contract violation triggers quarantine, not silent coercion; the close proceeds on the rest and reports the quarantine count |
| Corrupt / truncated file | Fail at ingest with a precise diagnostic; no partial state written |
| Duplicate UTRs | Detected pre-match, escalated as a typed exception rather than matched twice |
| Ambiguous match below tau | Never auto-posted; routed to the exception desk with the competing candidates shown |

## 10. Non-goals (explicit, so scope holds)

- No cash forecasting (Agent Studio ships it)
- No multi-PG / Optimizer ingestion
- No Postgres — DuckDB is sufficient and faster to stand up
- No OpenTelemetry
- No real Razorpay API calls; synthetic data only, stated plainly in the README
- Natural-language Q&A over the close is a **stretch goal only**

## 11. Stack

Python 3.11, FastAPI, Polars + DuckDB, scipy, scikit-learn (isotonic calibration),
pydantic v2, Claude Agent SDK, Next.js + TypeScript, pytest, Docker Compose.
One-command run: `docker compose up`.

## 12. Submission artifacts

- Public GitHub repo, clean commit history across the 3 days
- README: problem, architecture diagram, measured results table, how to run,
  *"Where we deliberately did not use AI, and why"*
- `EVALUATION.md`: full metrics, calibration plot, honest exception list
- 5-minute pitch video (unlisted)
- Form answer for "what broke": drawn from the chaos suite, with real output

## 13. Three-day plan

**Day 1 — make the numbers exist.** Scaffold, contracts, generator with all chaos
injectors and ground truth, normalisation, deterministic matcher (stages A + B),
eval harness printing real metrics. Everything after this improves a visible number.

**Day 2 — the differentiators.** N:M solver, isotonic calibration, tau from the cost
curve, policy gate, exception agent on Claude Agent SDK, hash-chained audit + replay.

**Day 3 — the submission.** Next.js dashboard (3 screens), chaos suite, README and
EVALUATION.md, pitch video, submit.

**Slip plan.** If the N:M solver slips on day 2, ship deterministic 1:1 + tolerance
matching with honest metrics and the exception list — this still clears the stated
bar — and the solver moves to a "what we'd build next" slide. Decided in advance so
it is a choice on day 2, not a panic on day 3.
