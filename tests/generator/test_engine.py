import json
from pathlib import Path

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate


def _small(**kw) -> GeneratorConfig:
    return GeneratorConfig(cycles=6, payments_per_cycle=10, **kw)


def test_generate_emits_four_files(tmp_path: Path):
    ds = generate(_small(), tmp_path)
    for p in (ds.razorpay_csv, ds.bank_csv, ds.erp_csv, ds.ground_truth_json):
        assert p.exists(), p
        assert p.stat().st_size > 0


def test_generation_is_deterministic_for_a_seed(tmp_path: Path):
    a = generate(_small(), tmp_path / "a")
    b = generate(_small(), tmp_path / "b")
    assert a.bank_csv.read_text() == b.bank_csv.read_text()
    assert a.ground_truth_json.read_text() == b.ground_truth_json.read_text()


def test_holdout_cycles_are_non_empty_and_unique(tmp_path: Path):
    ds = generate(_small(), tmp_path)
    assert len(ds.holdout_cycles) >= 1
    assert len(set(ds.holdout_cycles)) == len(ds.holdout_cycles)


def test_ground_truth_ids_all_exist_in_the_csvs(tmp_path: Path):
    ds = generate(_small(), tmp_path)
    links = json.loads(ds.ground_truth_json.read_text())
    bank_ids = {ln.split(",")[0] for ln in ds.bank_csv.read_text().splitlines()[1:]}
    rp_ids = {ln.split(",")[0] for ln in ds.razorpay_csv.read_text().splitlines()[1:]}
    for link in links:
        assert set(link["bank_ids"]) <= bank_ids
        assert set(link["razorpay_ids"]) <= rp_ids


def test_split_settlements_actually_occur(tmp_path: Path):
    ds = generate(_small(split_settlement_rate=1.0), tmp_path)
    links = json.loads(ds.ground_truth_json.read_text())
    assert any(len(link["razorpay_ids"]) > 1 for link in links)


def test_clean_config_produces_only_one_to_one_bank_links(tmp_path: Path):
    clean = GeneratorConfig(
        cycles=4, payments_per_cycle=8,
        split_settlement_rate=0.0, merged_credit_rate=0.0, refund_rate=0.0,
        chargeback_rate=0.0, duplicate_utr_rate=0.0, narration_corruption_rate=0.0,
        late_credit_rate=0.0, rounding_drift_rate=0.0, missing_row_rate=0.0,
    )
    ds = generate(clean, tmp_path)
    links = json.loads(ds.ground_truth_json.read_text())
    bank_links = [ln for ln in links if ln["bank_ids"]]
    assert bank_links
    assert all(len(ln["razorpay_ids"]) == 1 for ln in bank_links)


def test_duplicate_utrs_are_injected_when_enabled(tmp_path: Path):
    ds = generate(_small(duplicate_utr_rate=1.0), tmp_path)
    narrations = [ln.split(",")[2] for ln in ds.bank_csv.read_text().splitlines()[1:]]
    assert len(narrations) != len(set(narrations))
