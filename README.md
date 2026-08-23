# Nostro

Three-way settlement reconciliation for Indian merchants: Razorpay settlement report
against bank statement against ERP ledger. Full architecture, measured results, and
setup instructions land in Task 19; this file exists early to record stated assumptions
the matching engine relies on, so they are on the record rather than discovered later.

## Stated assumptions — subset-sum solver (Razorpay-to-bank axis)

The subset-sum solver (`src/nostro/match/solver.py`) that recovers split settlements
(one bank credit netting several Razorpay payments) relies on two assumptions, each
measured directly against the 411 genuine splits in `data/full` before being adopted
(411/411 on both counts — not tuned to flatter a result, but properties of what a
settlement batch actually is):

1. **A settlement batch is one cycle.** A subset is only ever built from candidate
   payments that share a single `CanonicalRow.settlement_cycle`. `match_subset_sums`
   groups its candidate window by cycle and searches each group independently.
2. **A bank credit equals the exact sum of its legs.** The generic
   `SolverConfig.residual_tolerance_paise` (100 paise) exists to absorb cross-source
   drift between Razorpay's net amounts and ERP's gross amounts — it does not apply on
   the Razorpay-to-bank axis. That axis instead uses the tight, dedicated
   `SolverConfig.bank_residual_tolerance_paise` (default 2 paise, configurable).

Both are stated as engine assumptions, not asserted as universal truths about every
possible merchant's settlement process — if a real-world bank ever splits a single
settlement cycle's payout across more than one credit, or nets across cycles, these
assumptions would need revisiting.
