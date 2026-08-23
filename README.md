# Nostro

Three-way settlement reconciliation for Indian merchants: Razorpay settlement report
against bank statement against ERP ledger. Full architecture, measured results, and
setup instructions land in Task 19; this file exists early to record stated assumptions
the matching engine relies on, so they are on the record rather than discovered later.

## Stated assumptions — subset-sum solver (Razorpay-to-bank axis)

The subset-sum solver (`src/nostro/match/solver.py`) that recovers split settlements
(one bank credit netting several Razorpay payments) relies on three assumptions. The
first two are domain facts, each measured directly against the 411 genuine splits in
`data/full` before being adopted (411/411 on both counts — not tuned to flatter a
result, but properties of what a settlement batch actually is). The third is an
honest, measured performance bound rather than a domain fact:

1. **A settlement batch is one cycle.** A subset is only ever built from candidate
   payments that share a single `CanonicalRow.settlement_cycle`. `match_subset_sums`
   groups its candidate window by cycle and searches each group independently.
2. **A bank credit equals the exact sum of its legs.** The generic
   `SolverConfig.residual_tolerance_paise` (100 paise) exists to absorb cross-source
   drift between Razorpay's net amounts and ERP's gross amounts — it does not apply on
   the Razorpay-to-bank axis. That axis instead uses the tight, dedicated
   `SolverConfig.bank_residual_tolerance_paise` (default 2 paise, configurable).
3. **The search is bounded to `max_candidates = 15` per settlement cycle, chosen from
   a measured sweep, not a guess.** Depth-first subset-sum is combinatorial in the
   pool size; sweeping `{12, 15, 20, 25}` on `data/full` showed all four complete well
   under a ~30-second wall-clock budget, so the value was picked on accuracy: 15 gives
   the best F1 (0.8116) of the four. Looser bounds recover more genuine splits (25
   recovers 50 of 411 vs 15's 24) but let precision fall as low as 0.877 by admitting
   more coincidental near-sum false positives — a reader can see exactly what a looser
   bound would have bought in the full sweep table in `task-10-report.md` (folded into
   `EVALUATION.md` in Task 19).

None of the three is asserted as a universal truth about every possible merchant's
settlement process — if a real-world bank ever splits a single settlement cycle's
payout across more than one credit, nets across cycles, or a merchant's cycles
regularly carry far more than 15 same-cycle legs within the date-blocking window,
these assumptions would need revisiting.
