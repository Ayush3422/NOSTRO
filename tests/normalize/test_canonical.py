from datetime import date

import pytest

from nostro.ingest.contracts import BankStatementRow, ErpSalesRow, RazorpaySettlementRow
from nostro.models import Direction, ParsedBy, Source
from nostro.normalize.canonical import CanonicalSet, to_canonical
from nostro.normalize.narration_parser import NarrationParser


def test_razorpay_row_canonicalises_on_net_amount():
    row = RazorpaySettlementRow(
        payment_id="pay_1", order_id="order_1", cycle="C000",
        captured_at=date(2026, 6, 1), settled_at=date(2026, 6, 3),
        gross_paise=100000, fee_paise=2000, gst_paise=360, net_paise=97640,
        entity_type="payment",
    )
    out = to_canonical([row], Source.RAZORPAY)[0]
    assert out.amount_paise == 97640
    assert out.direction is Direction.CREDIT
    assert out.value_date == date(2026, 6, 3)
    assert out.settlement_cycle == "C000"
    assert out.refs["order_id"] == "order_1"


def test_negative_razorpay_row_becomes_a_debit():
    row = RazorpaySettlementRow(
        payment_id="rfnd_1", order_id="order_1", cycle="C000",
        captured_at=date(2026, 6, 1), settled_at=date(2026, 6, 3),
        gross_paise=-100000, fee_paise=0, gst_paise=0, net_paise=-97640,
        entity_type="refund",
    )
    out = to_canonical([row], Source.RAZORPAY)[0]
    assert out.direction is Direction.DEBIT
    assert out.amount_paise == 97640


def test_bank_credit_carries_parsed_utr_and_provenance():
    row = BankStatementRow(
        txn_id="bk_1", value_date=date(2026, 6, 3),
        narration="NEFT CR-RAZORPAY SOFTWARE-UTR2608260001-RZPY SETTLEMENT",
        debit_paise=0, credit_paise=97640,
    )
    out = to_canonical([row], Source.BANK, NarrationParser())[0]
    assert out.refs["utr"] == "UTR2608260001"
    assert out.parsed_by is ParsedBy.REGEX
    assert out.direction is Direction.CREDIT
    assert out.narration_raw.startswith("NEFT CR")


def test_bank_debit_direction():
    row = BankStatementRow(
        txn_id="bk_2", value_date=date(2026, 6, 3),
        narration="CHARGEBACK DR RAZORPAY cb_1", debit_paise=5000, credit_paise=0,
    )
    out = to_canonical([row], Source.BANK, NarrationParser())[0]
    assert out.direction is Direction.DEBIT
    assert out.amount_paise == 5000
    assert out.refs["kind"] == "chargeback"


def test_erp_row_uses_gross_invoice_amount():
    row = ErpSalesRow(invoice_no="inv_1", order_id="order_1",
                      invoice_date=date(2026, 6, 1), invoice_paise=100000)
    out = to_canonical([row], Source.ERP)[0]
    assert out.amount_paise == 100000
    assert out.refs["order_id"] == "order_1"
    assert out.row_id == "inv_1"


def test_amounts_stay_int():
    row = ErpSalesRow(invoice_no="inv_1", order_id="o", invoice_date=date(2026, 6, 1),
                      invoice_paise=1)
    assert isinstance(to_canonical([row], Source.ERP)[0].amount_paise, int)


def test_by_id_raises_on_cross_source_row_id_collision():
    bank_row = BankStatementRow(
        txn_id="dup_1", value_date=date(2026, 6, 3),
        narration="NEFT CR-RAZORPAY", debit_paise=0, credit_paise=1000,
    )
    erp_row = ErpSalesRow(invoice_no="dup_1", order_id="order_1",
                          invoice_date=date(2026, 6, 1), invoice_paise=1000)
    cs = CanonicalSet(
        bank=to_canonical([bank_row], Source.BANK, NarrationParser()),
        erp=to_canonical([erp_row], Source.ERP),
    )
    with pytest.raises(ValueError, match="dup_1"):
        cs.by_id()


def test_by_id_returns_every_row_when_ids_are_unique():
    bank_row = BankStatementRow(
        txn_id="bk_unique", value_date=date(2026, 6, 3),
        narration="NEFT CR-RAZORPAY", debit_paise=0, credit_paise=1000,
    )
    erp_row = ErpSalesRow(invoice_no="inv_unique", order_id="order_1",
                          invoice_date=date(2026, 6, 1), invoice_paise=1000)
    rz_row = RazorpaySettlementRow(
        payment_id="pay_unique", order_id="order_1", cycle="C000",
        captured_at=date(2026, 6, 1), settled_at=date(2026, 6, 3),
        gross_paise=1000, fee_paise=0, gst_paise=0, net_paise=1000,
        entity_type="payment",
    )
    cs = CanonicalSet(
        razorpay=to_canonical([rz_row], Source.RAZORPAY),
        bank=to_canonical([bank_row], Source.BANK, NarrationParser()),
        erp=to_canonical([erp_row], Source.ERP),
    )
    index = cs.by_id()
    assert len(index) == 3
    assert set(index) == {"pay_unique", "bk_unique", "inv_unique"}
