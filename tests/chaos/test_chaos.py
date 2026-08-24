"""Injected-failure suite.

Each test answers one form of "what broke". The assertion in every case is the
same shape: correctness is preserved, automation degrades, and the degradation is
reported rather than hidden.
"""
from pathlib import Path

import pytest

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.ingest.loader import IngestError, load_csv
from nostro.models import Source
from nostro.normalize.narration_parser import NarrationParser
from nostro.pipeline import CloseConfig, run_close


@pytest.fixture
def dataset(tmp_path: Path):
    return generate(GeneratorConfig(cycles=8, payments_per_cycle=12), tmp_path / "data")


def _close(data_dir: Path, audit: Path, **kw):
    return run_close(CloseConfig(data_dir=data_dir, audit_path=audit,
                                 use_model=False, **kw), client=None)


class _ExplodingClient:
    class _Messages:
        def parse(self, **kwargs):
            raise TimeoutError("model unavailable")

    messages = _Messages()


class _StubResponse:
    """Same shape tests/exceptions/test_agent.py uses to stub a healthy call."""

    def __init__(self, parsed):
        self.parsed_output = parsed


class _HealthyClient:
    """A client whose messages.parse succeeds every time, returning a valid
    draft. Mirrors tests/exceptions/test_agent.py's _StubClient rather than
    inventing a new stub shape.
    """

    class _Messages:
        def parse(self, **kwargs):
            from nostro.exceptions.agent import _ResolutionDraft
            from nostro.exceptions.taxonomy import ResolutionKind
            return _StubResponse(_ResolutionDraft(
                kind=ResolutionKind.CHASE_COUNTERPARTY,
                rationale="ask the bank for the missing credit", confidence=0.8,
            ))

    messages = _Messages()


def test_model_outage_still_closes_the_books(dataset, tmp_path: Path):
    """Chaos 1 — the LLM is down mid-close."""
    baseline = _close(dataset.razorpay_csv.parent, tmp_path / "a.jsonl")
    degraded = run_close(
        CloseConfig(data_dir=dataset.razorpay_csv.parent,
                    audit_path=tmp_path / "b.jsonl", use_model=True),
        client=_ExplodingClient(),
    )
    # Same books. The model never touched a number.
    assert degraded.report.precision == baseline.report.precision
    assert degraded.report.recall == baseline.report.recall
    assert len(degraded.exceptions) == len(baseline.exceptions)


def test_model_outage_is_reported_as_degraded(dataset, tmp_path: Path):
    """Controller-authorised fix: a client that raises on every call must not
    be reported as a working model. Precision/recall being untouched (proven
    above) is necessary but not sufficient -- the dashboard also needs to
    know the model contributed nothing this close.
    """
    result = run_close(
        CloseConfig(data_dir=dataset.razorpay_csv.parent,
                    audit_path=tmp_path / "b2.jsonl", use_model=True),
        client=_ExplodingClient(),
    )
    assert "llm" in result.degraded


def test_a_healthy_model_is_not_reported_as_degraded(dataset, tmp_path: Path):
    """Negative case for the controller-authorised fix: a client whose
    messages.parse succeeds on every call must not be mislabelled as
    degraded. The flag drives what the dashboard tells a viewer about
    whether the close ran degraded, so a false positive here would
    mislabel every healthy close.
    """
    result = run_close(
        CloseConfig(data_dir=dataset.razorpay_csv.parent,
                    audit_path=tmp_path / "b3.jsonl", use_model=True),
        client=_HealthyClient(),
    )
    assert "llm" not in result.degraded


def test_narration_parser_survives_a_broken_model(dataset):
    def broken(_text):
        raise ConnectionError("model unavailable")

    parser = NarrationParser(llm_fallback=broken)
    rows = load_csv(dataset.bank_csv, Source.BANK).rows
    for row in rows:
        parser.parse(row.narration)          # must not raise
    assert parser.stats["regex_hits"] > 0


def test_schema_drift_quarantines_instead_of_coercing(dataset, tmp_path: Path):
    """Chaos 2 — the bank renames a column and mangles two amounts."""
    drifted = tmp_path / "drift"
    drifted.mkdir()
    for src in (dataset.razorpay_csv, dataset.erp_csv):
        (drifted / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    lines = dataset.bank_csv.read_text(encoding="utf-8").splitlines()
    for i in (2, 3):
        parts = lines[i].split(",")
        parts[4] = "N/A"
        lines[i] = ",".join(parts)
    (drifted / dataset.bank_csv.name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _close(drifted, tmp_path / "c.jsonl")
    assert result.quarantined_count == 2
    assert result.matches                     # the other rows still closed
    assert any(e.exception_class.value == "quarantined_row" for e in result.exceptions)


def test_a_missing_required_column_fails_loudly_at_ingest(dataset, tmp_path: Path):
    """Chaos 3 — a structurally wrong file must not produce a partial close."""
    broken = tmp_path / "broken.csv"
    broken.write_text("txn_id,narration\nbk_1,NEFT\n", encoding="utf-8")
    with pytest.raises(IngestError) as exc:
        load_csv(broken, Source.BANK)
    assert "value_date" in str(exc.value)


def test_duplicate_utrs_are_escalated_not_double_matched(tmp_path: Path):
    """Chaos 4 — the bank reuses a UTR across two credits.

    A Razorpay leg legitimately shows up in two matches at once — one on the
    Razorpay<->bank axis, one on the independent Razorpay<->ERP axis
    (pipeline.py: "the solver only works the Razorpay<->bank axis" and the
    reference pass separately claims rows "on the *other* (ERP) axis"). That
    is not double consumption, so the check has to be per axis.

    But what a duplicate UTR actually threatens is the shared side: two
    distinct bank rows B1/B2 sharing one UTR, with the matcher resolving the
    ambiguity wrong and producing two bank-axis matches for the same
    Razorpay row (razorpay_ids=[R1], bank_ids=[B1]) and
    (razorpay_ids=[R1], bank_ids=[B2]). B1 != B2, so a check that only
    tracks bank_ids/erp_ids sees no overlap and passes silently on exactly
    the scenario this test is named for. So each axis's seen-set must also
    carry razorpay_ids, checked and updated only against matches that
    belong to that axis (a match's razorpay leg legitimately participates
    in one bank-axis match AND one erp-axis match -- that combination must
    stay allowed).
    """
    ds = generate(GeneratorConfig(cycles=6, payments_per_cycle=10,
                                  duplicate_utr_rate=1.0), tmp_path / "dup")
    result = _close(ds.razorpay_csv.parent, tmp_path / "d.jsonl")
    seen_bank_axis: set[str] = set()
    seen_erp_axis: set[str] = set()
    for match in result.matches:
        if match.bank_ids:
            touched = set(match.razorpay_ids) | set(match.bank_ids)
            assert not (touched & seen_bank_axis), \
                "a Razorpay or bank row was consumed by two bank-axis matches"
            seen_bank_axis |= touched
        if match.erp_ids:
            touched = set(match.razorpay_ids) | set(match.erp_ids)
            assert not (touched & seen_erp_axis), \
                "a Razorpay or ERP row was consumed by two ERP-axis matches"
            seen_erp_axis |= touched


def test_the_audit_ledger_still_verifies_after_a_degraded_close(dataset, tmp_path: Path):
    from nostro.audit.ledger import Ledger
    audit = tmp_path / "e.jsonl"
    _close(dataset.razorpay_csv.parent, audit)
    ok, bad = Ledger(audit).verify()
    assert ok is True and bad is None


def test_an_all_chaos_dataset_still_produces_a_complete_exception_list(tmp_path: Path):
    """Every injector at maximum. Nothing may be silently dropped."""
    ds = generate(GeneratorConfig(
        cycles=8, payments_per_cycle=12, split_settlement_rate=0.9,
        duplicate_utr_rate=0.5, narration_corruption_rate=0.9, late_credit_rate=0.4,
        rounding_drift_rate=0.5, missing_row_rate=0.15, chargeback_rate=0.1,
    ), tmp_path / "storm")
    result = _close(ds.razorpay_csv.parent, tmp_path / "f.jsonl")
    matched = set()
    for m in result.matches:
        matched.update(m.razorpay_ids + m.bank_ids + m.erp_ids)
    excepted = {rid for e in result.exceptions for rid in e.row_ids}
    total = ds.row_counts["razorpay"] + ds.row_counts["bank"] + ds.row_counts["erp"]
    assert len(matched | excepted) >= total - result.quarantined_count
