"""The close, end to end.

Stage order matters and is the argument of the whole project:

    ingest -> normalise -> block -> deterministic -> subset-sum
           -> score -> calibrate -> gate -> exceptions -> evaluate

Everything before `exceptions` is deterministic. The model appears once, at the
exception desk, after every number has already been computed. Turning it off
changes how much explanation you get, never what the books say.

Held-out split: the calibrator and the auto-post threshold (tau) are both fit
using ground-truth links whose Razorpay leg falls OUTSIDE `cfg.holdout_cycles`
("train" links) only. `EvalReport` is measured against the holdout links when
`cfg.holdout_cycles` is non-empty; it falls back to all links, explicitly,
when the config carries no holdout cycles at all (e.g. ad-hoc runs against a
dataset that was not generated with a split in mind). No number derived from
holdout links is ever allowed to influence `fit` or `choose_tau` — that is the
entire point of the split, and the reason the headline numbers this pipeline
reports are allowed to be trusted.
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, Field

from nostro.audit.ledger import Ledger
from nostro.eval.harness import EvalReport, evaluate, filter_to_cycles, load_ground_truth
from nostro.exceptions.agent import ExceptionDesk
from nostro.exceptions.taxonomy import ExceptionItem, build_exceptions
from nostro.ingest.loader import load_csv
from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.calibrate import Calibrator, label_matches
from nostro.match.deterministic import match_deterministic
from nostro.match.scoring import extract_features, raw_score
from nostro.match.solver import SolverConfig, match_subset_sums
from nostro.models import Match, Source
from nostro.normalize.canonical import CanonicalSet, to_canonical
from nostro.normalize.narration_parser import NarrationParser
from nostro.policy.gate import CostModel, Decision, ThresholdChoice, choose_tau, decide


class CloseConfig(BaseModel):
    data_dir: Path
    audit_path: Path = Path("data/audit.jsonl")
    blocking: BlockingConfig = Field(default_factory=BlockingConfig)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    costs: CostModel = Field(default_factory=CostModel)
    holdout_cycles: tuple[str, ...] = ()
    use_model: bool = True


class CloseResult(BaseModel):
    matches: list[Match]
    probabilities: list[float]
    exceptions: list[ExceptionItem]
    threshold: ThresholdChoice
    report: EvalReport | None
    # `report.match_rate` in holdout mode is biased upward: `_restrict_to_holdout`
    # builds its bank/ERP rows as exactly the rows the holdout matches touched
    # (bank/ERP rows carry no settlement_cycle, so there is no other way to
    # scope them to the holdout population), which makes the numerator and
    # denominator tautologically equal on those two sources. Only unmatched
    # Razorpay rows can pull that number down, so it is not comparable to an
    # in-sample match_rate and must not be published as one. This field is
    # the honestly-scoped alternative: match rate over the Razorpay side only,
    # where cycle membership is well defined. None when cfg.holdout_cycles is
    # empty (there is no holdout population to report).
    holdout_razorpay_match_rate: float | None
    quarantined_count: int
    parser_stats: dict[str, int]
    result_hash: str
    auto_posted: int
    degraded: list[str]


def _partition_matches_by_cycle(
    matches: list[Match], cset: CanonicalSet, holdout_cycles: tuple[str, ...]
) -> tuple[list[Match], list[Match]]:
    """Partition matches into (train, holdout) by their Razorpay leg's cycle.

    This is the population-matching primitive both splits rest on: whatever
    predictions are compared against a set of truth links -- fitting the
    calibrator against train links, choosing tau against train links, or
    evaluating against holdout links -- must themselves be drawn from that
    same cycle population, or the comparison measures a population mismatch
    instead of anything real. (Round 1 fixed this on the holdout/evaluate
    side; round 2 fixes the identical bug on the train/fit side, where only
    the *labels* were being restricted while the full match list -- both
    train and holdout matches -- still went into `fit` and `choose_tau`. A
    holdout-cycle match's pairs can never appear in train-only truth by
    construction, so every one of them, including genuinely correct
    matches, was being forced to label 0 -- contaminating the isotonic fit
    and the cost sweep with several hundred manufactured false negatives.)

    Every match in this system carries a Razorpay leg (matching is always
    Razorpay<->bank or Razorpay<->ERP), so this partition is expected to be
    total and clean rather than drop some matches to neither side. That is
    checked, not assumed: a match with no Razorpay leg raises, because it
    would mean this invariant no longer holds.
    """
    wanted = set(holdout_cycles)
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}

    missing_leg = [m.match_id for m in matches if not m.razorpay_ids]
    if missing_leg:
        raise AssertionError(
            "every match is expected to carry a Razorpay leg (matching is "
            "always Razorpay<->bank or Razorpay<->ERP); found matches "
            f"without one, so the train/holdout population split cannot be "
            f"trusted: {missing_leg[:5]}"
        )

    holdout_matches = [
        m for m in matches
        if any(cycle_of.get(rid) in wanted for rid in m.razorpay_ids)
    ]
    holdout_match_ids = {m.match_id for m in holdout_matches}
    train_matches = [m for m in matches if m.match_id not in holdout_match_ids]
    return train_matches, holdout_matches


def _restrict_to_holdout(
    matches: list[Match], cset: CanonicalSet, holdout_cycles: tuple[str, ...]
) -> tuple[list[Match], CanonicalSet]:
    """Restrict predictions and rows to the holdout population, together.

    Precision, recall, and match_rate are only meaningful when the predicted
    set and the truth set are drawn from the same population. Filters
    `matches` to those whose Razorpay leg's settlement_cycle falls in
    `holdout_cycles`, and builds a `CanonicalSet` containing exactly the
    holdout Razorpay rows plus the bank/ERP rows those filtered matches
    touch -- not the full dataset, which would silently reintroduce the same
    mismatch on the row-based metrics.
    """
    _, holdout_matches = _partition_matches_by_cycle(matches, cset, holdout_cycles)

    wanted = set(holdout_cycles)
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}
    holdout_razorpay_ids = {rid for rid, cyc in cycle_of.items() if cyc in wanted}
    touched_bank_ids: set[str] = set()
    touched_erp_ids: set[str] = set()
    for m in holdout_matches:
        touched_bank_ids.update(m.bank_ids)
        touched_erp_ids.update(m.erp_ids)

    eval_cset = CanonicalSet(
        razorpay=[r for r in cset.razorpay if r.row_id in holdout_razorpay_ids],
        bank=[r for r in cset.bank if r.row_id in touched_bank_ids],
        erp=[r for r in cset.erp if r.row_id in touched_erp_ids],
    )
    return holdout_matches, eval_cset


def _restrict_to_train(
    matches: list[Match], cset: CanonicalSet, holdout_cycles: tuple[str, ...]
) -> list[Match]:
    """The other half of the split: predictions used to fit the calibrator
    and choose tau must themselves be train-cycle matches, not the full
    match list labelled with train-only truth. See
    `_partition_matches_by_cycle` for why that distinction is load-bearing.
    """
    train_matches, _ = _partition_matches_by_cycle(matches, cset, holdout_cycles)
    return train_matches


def run_close(cfg: CloseConfig, client=None) -> CloseResult:
    started = perf_counter()
    degraded: list[str] = []
    ledger = Ledger(cfg.audit_path)
    ledger.append("close_started", {"data_dir": str(cfg.data_dir)})

    # --- ingest -----------------------------------------------------------
    parser = NarrationParser()
    rp = load_csv(cfg.data_dir / "razorpay_settlement.csv", Source.RAZORPAY)
    bk = load_csv(cfg.data_dir / "bank_statement.csv", Source.BANK)
    erp = load_csv(cfg.data_dir / "erp_sales.csv", Source.ERP)
    quarantined = [*rp.quarantined, *bk.quarantined, *erp.quarantined]
    if quarantined:
        ledger.append("rows_quarantined", {"count": len(quarantined)})

    cset = CanonicalSet(
        razorpay=to_canonical(rp.rows, Source.RAZORPAY),
        bank=to_canonical(bk.rows, Source.BANK, parser),
        erp=to_canonical(erp.rows, Source.ERP),
    )

    # --- match ------------------------------------------------------------
    blocks = build_blocks(cset, cfg.blocking)
    deterministic = match_deterministic(cset, blocks, cfg.blocking)
    # bank_consumed, not the union `consumed`: the solver only works the
    # Razorpay<->bank axis, and the union would starve it of rows the
    # reference pass legitimately claimed on the *other* (ERP) axis.
    solved = match_subset_sums(cset, blocks, deterministic.bank_consumed, cfg.solver)
    matches = deterministic.matches + solved
    ledger.append("matching_complete", {
        "deterministic": len(deterministic.matches), "subset_sum": len(solved),
    })

    consumed = set(deterministic.consumed)
    for match in solved:
        consumed.update(match.razorpay_ids + match.bank_ids + match.erp_ids)

    # --- score --------------------------------------------------------------
    scores = [raw_score(extract_features(m, blocks)) for m in matches]

    gt_path = cfg.data_dir / "ground_truth.json"
    links = load_ground_truth(gt_path) if gt_path.exists() else []

    # --- calibrate + gate, on the TRAIN split only -------------------------
    # The held-out split is by settlement cycle: a link belongs to holdout
    # when its Razorpay leg's cycle is in cfg.holdout_cycles; everything else
    # is train. Both the LABELS (from train links) and the PREDICTIONS (train-
    # cycle matches only) that go into `fit` and `choose_tau` must be
    # restricted -- restricting only the labels while still fitting/choosing
    # on the full match list forces every holdout-cycle match, including
    # correct ones, to label 0 (its truth lives in the holdout links, which
    # never reach `train_links`), manufacturing false negatives that
    # contaminate the fit. See `_partition_matches_by_cycle`.
    holdout_links = filter_to_cycles(links, cset, cfg.holdout_cycles) if links else []
    holdout_ids = {id(link) for link in holdout_links}
    train_links = [link for link in links if id(link) not in holdout_ids]

    train_matches = (
        _restrict_to_train(matches, cset, cfg.holdout_cycles)
        if cfg.holdout_cycles else matches
    )

    if train_matches and train_links:
        train_labels = label_matches(train_matches, train_links)
        train_scores = [raw_score(extract_features(m, blocks)) for m in train_matches]
        calibrator = Calibrator().fit(train_scores, train_labels)
        probabilities = calibrator.predict(scores)          # applied to every match
        train_probabilities = calibrator.predict(train_scores)
        threshold = choose_tau(train_probabilities, train_labels, cfg.costs)
    else:
        degraded.append("calibration")
        probabilities = scores
        threshold = choose_tau([], [], cfg.costs)

    auto_posted = sum(
        1 for p in probabilities if decide(p, threshold.tau) is Decision.AUTO_POST
    )
    ledger.append("threshold_chosen", {
        "tau": threshold.tau, "auto_posted": auto_posted,
        "expected_cost_paise": threshold.expected_cost_paise,
    })

    # --- exceptions -------------------------------------------------------
    exceptions = build_exceptions(cset, matches, quarantined, consumed)
    ledger.append("exceptions_built", {"count": len(exceptions)})

    if cfg.use_model and client is not None:
        desk = ExceptionDesk(client=client, ledger=ledger)
        for item in exceptions[:50]:          # bounded: the desk is the expensive part
            desk.propose(item, cset)
    else:
        degraded.append("llm")

    # --- evaluate, on the HOLDOUT split -------------------------------------
    # Report on holdout links whenever the config declares holdout cycles at
    # all; fall back to reporting on every link, explicitly, only when it
    # doesn't (there is no held-out/train distinction to make in that case).
    #
    # A held-out evaluation must restrict BOTH sides to the same population:
    # comparing the FULL match list (mostly train-cycle predictions) against
    # holdout-ONLY truth counts every correct train-cycle match as a false
    # positive, because it can never appear in holdout truth by construction.
    # That is a population mismatch, not a measurement of anything -- so
    # `_restrict_to_holdout` filters predictions to the holdout population
    # too before `evaluate` ever sees them.
    elapsed = perf_counter() - started
    if cfg.holdout_cycles:
        eval_matches, eval_cset = _restrict_to_holdout(matches, cset, cfg.holdout_cycles)
        eval_links = holdout_links
        # Razorpay-only match rate over the holdout population: the only side
        # of `eval_cset` where cycle membership is well defined, and the only
        # side where "matched" and "total" aren't tautologically equal by
        # construction (see the field docstring on CloseResult).
        matched_razorpay_ids = {rid for m in eval_matches for rid in m.razorpay_ids}
        holdout_razorpay_ids = {r.row_id for r in eval_cset.razorpay}
        holdout_razorpay_match_rate = (
            len(matched_razorpay_ids & holdout_razorpay_ids) / len(holdout_razorpay_ids)
            if holdout_razorpay_ids else 0.0
        )
    else:
        eval_matches, eval_cset = matches, cset
        eval_links = links      # explicit fallback: no holdout split configured
        holdout_razorpay_match_rate = None
    report = evaluate(eval_matches, eval_links, eval_cset, elapsed) if eval_links else None
    if report is not None:
        # Never write the whole-population match_rate into the permanent record
        # when it is the biased holdout figure (see CloseResult's docstring on
        # holdout_razorpay_match_rate): an audit trail may legitimately show
        # history, but it should not be handed a number we've already disowned.
        # evaluation_mode records which regime produced these figures, the same
        # distinction the API surfaces.
        evaluation_mode = "holdout" if holdout_razorpay_match_rate is not None else "in_sample"
        ledger.append("evaluated", {
            "precision": report.precision, "recall": report.recall, "f1": report.f1,
            "evaluation_mode": evaluation_mode,
            "match_rate": (
                holdout_razorpay_match_rate if evaluation_mode == "holdout"
                else report.match_rate
            ),
        })

    return CloseResult(
        matches=matches, probabilities=probabilities, exceptions=exceptions,
        threshold=threshold, report=report,
        holdout_razorpay_match_rate=holdout_razorpay_match_rate,
        quarantined_count=len(quarantined),
        parser_stats=parser.stats, result_hash=ledger.result_hash(),
        auto_posted=auto_posted, degraded=degraded,
    )
