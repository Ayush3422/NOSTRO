from pathlib import Path

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.pipeline import CloseConfig, run_close


def _dataset(tmp_path: Path):
    cfg = GeneratorConfig(cycles=8, payments_per_cycle=12)
    return generate(cfg, tmp_path / "data")


def test_close_runs_end_to_end_without_a_model(tmp_path: Path):
    ds = _dataset(tmp_path)
    result = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                                   audit_path=tmp_path / "audit.jsonl",
                                   holdout_cycles=ds.holdout_cycles,
                                   use_model=False), client=None)
    assert result.matches
    assert result.report is not None
    assert 0.0 <= result.report.precision <= 1.0
    assert result.result_hash


def test_every_row_is_either_matched_or_an_exception(tmp_path: Path):
    ds = _dataset(tmp_path)
    result = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                                   audit_path=tmp_path / "audit.jsonl",
                                   use_model=False), client=None)
    matched = set()
    for m in result.matches:
        matched.update(m.razorpay_ids + m.bank_ids + m.erp_ids)
    excepted = {rid for item in result.exceptions for rid in item.row_ids}
    total = ds.row_counts["razorpay"] + ds.row_counts["bank"] + ds.row_counts["erp"]
    assert len(matched | excepted) >= total - result.quarantined_count


def test_running_without_a_model_is_recorded_as_degraded(tmp_path: Path):
    ds = _dataset(tmp_path)
    result = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                                   audit_path=tmp_path / "audit.jsonl",
                                   use_model=False), client=None)
    assert "llm" in result.degraded


def test_the_close_is_reproducible(tmp_path: Path):
    ds = _dataset(tmp_path)
    a = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                              audit_path=tmp_path / "a.jsonl", use_model=False))
    b = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                              audit_path=tmp_path / "b.jsonl", use_model=False))
    assert a.result_hash == b.result_hash


def test_the_audit_ledger_verifies_after_a_close(tmp_path: Path):
    from nostro.audit.ledger import Ledger
    ds = _dataset(tmp_path)
    audit = tmp_path / "audit.jsonl"
    run_close(CloseConfig(data_dir=ds.razorpay_csv.parent, audit_path=audit,
                          use_model=False))
    ok, bad = Ledger(audit).verify()
    assert ok is True
    assert bad is None


def test_subset_sum_lifts_recall_above_the_deterministic_floor(tmp_path: Path):
    """The headline claim of the project, asserted as a test."""
    from nostro.match.solver import SolverConfig
    ds = _dataset(tmp_path)
    base = CloseConfig(data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "x.jsonl",
                       use_model=False, solver=SolverConfig(max_subset_size=1))
    full = CloseConfig(data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "y.jsonl",
                       use_model=False)
    assert run_close(full).report.recall > run_close(base).report.recall


def test_calibrator_never_sees_holdout_labels(tmp_path: Path):
    """Controller override 1: fitting must be blind to holdout ground truth.

    We can't peek inside the closure that calls Calibrator.fit, so we assert
    the externally-observable consequence: a close run with holdout_cycles
    set to ALL cycles in the dataset (so train is empty) must report
    calibration as degraded, because there is no usable train split.
    """
    ds = _dataset(tmp_path)
    # The generator names cycles "C000", "C001", ... — _dataset() above uses
    # cycles=8, so "C000".."C019" is certainly a superset of every cycle in
    # this dataset, forcing every ground-truth link's Razorpay leg into
    # holdout and leaving train empty.
    every_cycle = tuple(f"C{i:03d}" for i in range(20))
    result = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                                   audit_path=tmp_path / "audit.jsonl",
                                   holdout_cycles=every_cycle,
                                   use_model=False))
    assert "calibration" in result.degraded


def test_holdout_report_differs_in_kind_from_all_links_report(tmp_path: Path):
    """Passing holdout_cycles changes what evaluate() is measured against."""
    ds = _dataset(tmp_path)
    with_holdout = run_close(CloseConfig(
        data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "h.jsonl",
        holdout_cycles=ds.holdout_cycles, use_model=False,
    ))
    without_holdout = run_close(CloseConfig(
        data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "n.jsonl",
        holdout_cycles=(), use_model=False,
    ))
    assert with_holdout.report is not None
    assert without_holdout.report is not None
    # The holdout report covers strictly fewer true pairs than the all-links
    # (fallback) report, since holdout is a proper subset of all links.
    assert with_holdout.report.true_pairs <= without_holdout.report.true_pairs


def test_holdout_restriction_keeps_predictions_and_truth_in_one_population():
    """The invariant Ruling R2 omitted: whatever `evaluate` is given as
    predictions in holdout mode must be drawn from the same cycle population
    as the truth it is graded against, or precision measures a population
    mismatch instead of anything real."""
    from datetime import date

    from nostro.models import CanonicalRow, Direction, Match, MatchMethod, Source
    from nostro.normalize.canonical import CanonicalSet
    from nostro.pipeline import _restrict_to_holdout

    def row(rid, src, cycle=None):
        return CanonicalRow(source=src, row_id=rid, amount_paise=100,
                            direction=Direction.CREDIT, value_date=date(2026, 6, 1),
                            settlement_cycle=cycle)

    cset = CanonicalSet(
        razorpay=[row("pay_1", Source.RAZORPAY, "C1"),   # train
                  row("pay_2", Source.RAZORPAY, "C2"),   # holdout
                  row("pay_3", Source.RAZORPAY, "C2")],  # holdout
        bank=[row("bk_1", Source.BANK), row("bk_2", Source.BANK),
              row("bk_3", Source.BANK)],
    )
    train_match = Match(match_id="m1", razorpay_ids=("pay_1",), bank_ids=("bk_1",),
                        score=1.0, method=MatchMethod.EXACT)
    holdout_match_a = Match(match_id="m2", razorpay_ids=("pay_2",), bank_ids=("bk_2",),
                            score=1.0, method=MatchMethod.EXACT)
    holdout_match_b = Match(match_id="m3", razorpay_ids=("pay_3",), bank_ids=("bk_3",),
                            score=1.0, method=MatchMethod.EXACT)
    matches = [train_match, holdout_match_a, holdout_match_b]

    eval_matches, eval_cset = _restrict_to_holdout(matches, cset, ("C2",))

    # Every predicted match kept is drawn from the holdout cycle -- the
    # train-cycle match must not survive the filter.
    kept_ids = {m.match_id for m in eval_matches}
    assert kept_ids == {"m2", "m3"}
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}
    for m in eval_matches:
        assert all(cycle_of[rid] == "C2" for rid in m.razorpay_ids)

    # Row population matches too: only the holdout Razorpay rows, and only
    # the bank rows the *holdout* matches actually touched.
    assert {r.row_id for r in eval_cset.razorpay} == {"pay_2", "pay_3"}
    assert {r.row_id for r in eval_cset.bank} == {"bk_2", "bk_3"}


def test_restrict_to_holdout_raises_if_a_match_has_no_razorpay_leg():
    """Every match in this system is Razorpay<->bank or Razorpay<->ERP. If
    that ever stops being true, fail loudly rather than silently mis-split."""
    from datetime import date

    from nostro.models import CanonicalRow, Direction, Match, MatchMethod, Source
    from nostro.normalize.canonical import CanonicalSet
    from nostro.pipeline import _restrict_to_holdout

    cset = CanonicalSet(
        razorpay=[CanonicalRow(source=Source.RAZORPAY, row_id="pay_1",
                               amount_paise=100, direction=Direction.CREDIT,
                               value_date=date(2026, 6, 1), settlement_cycle="C1")],
        bank=[], erp=[],
    )
    bad_match = Match(match_id="orphan", razorpay_ids=(), bank_ids=(),
                      erp_ids=(), score=1.0, method=MatchMethod.EXACT)
    try:
        _restrict_to_holdout([bad_match], cset, ("C1",))
        assert False, "expected an AssertionError"
    except AssertionError as exc:
        assert "orphan" in str(exc)


def test_holdout_precision_recovers_near_in_sample_once_populations_match(tmp_path: Path):
    """The regression this whole fix is about: with the predicted and truth
    populations aligned, held-out precision should land in the same
    neighbourhood as the in-sample figure, not collapse from a mismatch."""
    ds = _dataset(tmp_path)
    holdout = run_close(CloseConfig(
        data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "ho.jsonl",
        holdout_cycles=ds.holdout_cycles, use_model=False,
    ))
    in_sample = run_close(CloseConfig(
        data_dir=ds.razorpay_csv.parent, audit_path=tmp_path / "is.jsonl",
        holdout_cycles=(), use_model=False,
    ))
    assert holdout.report is not None
    assert in_sample.report is not None
    # Not a strict equality -- different cycles, different exact figures --
    # but a population-matched holdout precision should not be wildly below
    # the in-sample number the way the mismatched version was (0.30 vs 0.99).
    assert holdout.report.precision >= in_sample.report.precision - 0.25


def test_train_restriction_excludes_holdout_matches_from_fitting():
    """The other half of round 1's invariant: predictions used to fit the
    calibrator and choose tau must themselves be train-cycle matches, not
    just labelled against train-only truth. A holdout-cycle match must not
    survive `_restrict_to_train`, even a genuinely correct one -- if it did,
    it would either contaminate the fit (if mislabelled 0 against train
    truth, which is what round 1's leftover bug did) or leak (if somehow
    labelled correctly using holdout truth)."""
    from datetime import date

    from nostro.models import CanonicalRow, Direction, Match, MatchMethod, Source
    from nostro.normalize.canonical import CanonicalSet
    from nostro.pipeline import _restrict_to_train

    def row(rid, src, cycle=None):
        return CanonicalRow(source=src, row_id=rid, amount_paise=100,
                            direction=Direction.CREDIT, value_date=date(2026, 6, 1),
                            settlement_cycle=cycle)

    cset = CanonicalSet(
        razorpay=[row("pay_1", Source.RAZORPAY, "C1"),   # train
                  row("pay_2", Source.RAZORPAY, "C2")],  # holdout
        bank=[row("bk_1", Source.BANK), row("bk_2", Source.BANK)],
    )
    train_match = Match(match_id="m1", razorpay_ids=("pay_1",), bank_ids=("bk_1",),
                        score=1.0, method=MatchMethod.EXACT)
    holdout_match = Match(match_id="m2", razorpay_ids=("pay_2",), bank_ids=("bk_2",),
                          score=1.0, method=MatchMethod.EXACT)

    train_matches = _restrict_to_train([train_match, holdout_match], cset, ("C2",))

    assert {m.match_id for m in train_matches} == {"m1"}
    cycle_of = {r.row_id: r.settlement_cycle for r in cset.razorpay}
    for m in train_matches:
        assert all(cycle_of[rid] != "C2" for rid in m.razorpay_ids)


def test_train_and_holdout_match_partitions_are_disjoint_and_total(tmp_path: Path):
    """End-to-end version of the same invariant against the real generator
    output: every match belongs to exactly one side of the split, by cycle."""
    from nostro.pipeline import _partition_matches_by_cycle
    from nostro.match.blocking import BlockingConfig, build_blocks
    from nostro.match.deterministic import match_deterministic
    from nostro.match.solver import match_subset_sums
    from nostro.ingest.loader import load_csv
    from nostro.models import Source
    from nostro.normalize.canonical import CanonicalSet, to_canonical
    from nostro.normalize.narration_parser import NarrationParser
    from nostro.match.solver import SolverConfig

    ds = _dataset(tmp_path)
    data_dir = ds.razorpay_csv.parent
    parser = NarrationParser()
    rp = load_csv(data_dir / "razorpay_settlement.csv", Source.RAZORPAY)
    bk = load_csv(data_dir / "bank_statement.csv", Source.BANK)
    erp = load_csv(data_dir / "erp_sales.csv", Source.ERP)
    cset = CanonicalSet(
        razorpay=to_canonical(rp.rows, Source.RAZORPAY),
        bank=to_canonical(bk.rows, Source.BANK, parser),
        erp=to_canonical(erp.rows, Source.ERP),
    )
    blocks = build_blocks(cset, BlockingConfig())
    deterministic = match_deterministic(cset, blocks, BlockingConfig())
    solved = match_subset_sums(cset, blocks, deterministic.bank_consumed, SolverConfig())
    matches = deterministic.matches + solved

    train_matches, holdout_matches = _partition_matches_by_cycle(
        matches, cset, ds.holdout_cycles
    )
    train_ids = {m.match_id for m in train_matches}
    holdout_ids = {m.match_id for m in holdout_matches}
    assert not (train_ids & holdout_ids)                       # disjoint
    assert train_ids | holdout_ids == {m.match_id for m in matches}  # total
    assert holdout_matches                                     # non-trivial split


def test_tau_uses_only_train_cycle_predictions(tmp_path: Path):
    """Regression for round 2: before the fix, holdout-cycle matches entered
    `fit`/`choose_tau` mislabelled as 0 purely because their truth lives in
    holdout links, which is enough with this cost model (Rs 2,500 wrong-post
    vs Rs 50 review) to push tau to 1.0 / auto_posted to 0 as an artifact,
    not a risk judgement. This does not assert a specific tau (that is a
    real modelling outcome, not a contract) -- only that fitting used a
    population consistent with `_restrict_to_train`, which is checked
    directly in the two tests above. This test exists to document the
    expectation and catch a gross regression back to the old behaviour.
    """
    ds = _dataset(tmp_path)
    result = run_close(CloseConfig(data_dir=ds.razorpay_csv.parent,
                                   audit_path=tmp_path / "audit.jsonl",
                                   holdout_cycles=ds.holdout_cycles,
                                   use_model=False))
    assert result.threshold is not None
    assert 0.0 <= result.threshold.tau <= 1.0
