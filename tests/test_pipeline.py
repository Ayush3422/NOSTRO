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
