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
    quarantined_count: int
    parser_stats: dict[str, int]
    result_hash: str
    auto_posted: int
    degraded: list[str]


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

    Every match in this system carries a Razorpay leg (matching is always
    Razorpay<->bank or Razorpay<->ERP), so this filter is expected to
    partition matches cleanly by cycle rather than drop some to neither
    side. That is checked, not assumed: a match with no Razorpay leg raises,
    because it would mean this invariant no longer holds.
    """
    wanted = set(holdout_cycles)
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}

    missing_leg = [m.match_id for m in matches if not m.razorpay_ids]
    if missing_leg:
        raise AssertionError(
            "every match is expected to carry a Razorpay leg (matching is "
            "always Razorpay<->bank or Razorpay<->ERP); found matches "
            f"without one, so the holdout population split cannot be "
            f"trusted: {missing_leg[:5]}"
        )

    eval_matches = [
        m for m in matches
        if any(cycle_of.get(rid) in wanted for rid in m.razorpay_ids)
    ]

    holdout_razorpay_ids = {rid for rid, cyc in cycle_of.items() if cyc in wanted}
    touched_bank_ids: set[str] = set()
    touched_erp_ids: set[str] = set()
    for m in eval_matches:
        touched_bank_ids.update(m.bank_ids)
        touched_erp_ids.update(m.erp_ids)

    eval_cset = CanonicalSet(
        razorpay=[r for r in cset.razorpay if r.row_id in holdout_razorpay_ids],
        bank=[r for r in cset.bank if r.row_id in touched_bank_ids],
        erp=[r for r in cset.erp if r.row_id in touched_erp_ids],
    )
    return eval_matches, eval_cset


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
    # is train. The calibrator's `fit` and the tau search in `choose_tau` may
    # only ever see labels derived from train links -- holdout ground truth
    # must never reach either call, or every headline number this pipeline
    # reports would be optimistic.
    holdout_links = filter_to_cycles(links, cset, cfg.holdout_cycles) if links else []
    holdout_ids = {id(link) for link in holdout_links}
    train_links = [link for link in links if id(link) not in holdout_ids]

    if train_links:
        train_labels = label_matches(matches, train_links)
        calibrator = Calibrator().fit(scores, train_labels)
        probabilities = calibrator.predict(scores)
        threshold = choose_tau(probabilities, train_labels, cfg.costs)
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
    else:
        eval_matches, eval_cset = matches, cset
        eval_links = links      # explicit fallback: no holdout split configured
    report = evaluate(eval_matches, eval_links, eval_cset, elapsed) if eval_links else None
    if report is not None:
        ledger.append("evaluated", {
            "precision": report.precision, "recall": report.recall,
            "f1": report.f1, "match_rate": report.match_rate,
        })

    return CloseResult(
        matches=matches, probabilities=probabilities, exceptions=exceptions,
        threshold=threshold, report=report, quarantined_count=len(quarantined),
        parser_stats=parser.stats, result_hash=ledger.result_hash(),
        auto_posted=auto_posted, degraded=degraded,
    )
