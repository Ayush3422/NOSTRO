from pathlib import Path
import pytest
from nostro.ingest.loader import IngestError, load_csv
from nostro.models import Source

BANK_HEADER = "txn_id,value_date,narration,debit,credit,balance\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_clean_bank_rows_load(tmp_path: Path):
    p = _write(tmp_path, "b.csv", BANK_HEADER + "bk_1,2026-06-03,NEFT CR UTR1,,100.50,\n")
    result = load_csv(p, Source.BANK)
    assert len(result.rows) == 1
    assert result.rows[0].credit_paise == 10050
    assert result.quarantined == []


def test_bad_amount_is_quarantined_not_coerced(tmp_path: Path):
    p = _write(tmp_path, "b.csv", BANK_HEADER + "bk_1,2026-06-03,NEFT,,N/A,\n")
    result = load_csv(p, Source.BANK)
    assert result.rows == []
    assert len(result.quarantined) == 1
    assert result.quarantined[0].line_no == 2
    assert "N/A" in result.quarantined[0].reason


def test_one_bad_row_does_not_stop_the_close(tmp_path: Path):
    p = _write(tmp_path, "b.csv", BANK_HEADER
               + "bk_1,2026-06-03,A,,10.00,\nbk_2,NOT-A-DATE,B,,20.00,\n"
               + "bk_3,2026-06-04,C,,30.00,\n")
    result = load_csv(p, Source.BANK)
    assert len(result.rows) == 2
    assert len(result.quarantined) == 1


def test_missing_required_header_is_a_file_level_error(tmp_path: Path):
    p = _write(tmp_path, "b.csv", "txn_id,narration\nbk_1,x\n")
    with pytest.raises(IngestError) as exc:
        load_csv(p, Source.BANK)
    assert "value_date" in str(exc.value)


def test_extra_unknown_column_is_tolerated(tmp_path: Path):
    p = _write(tmp_path, "b.csv", BANK_HEADER.rstrip("\n") + ",bank_ref\n"
               + "bk_1,2026-06-03,NEFT,,10.00,,XYZ\n")
    result = load_csv(p, Source.BANK)
    assert len(result.rows) == 1


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(IngestError):
        load_csv(tmp_path / "nope.csv", Source.BANK)
