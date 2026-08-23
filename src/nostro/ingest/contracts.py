"""Per-source contracts. A source row that does not satisfy its contract is
quarantined; it is never repaired, defaulted, or coerced into shape."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator

from nostro.money import rupees_to_paise

REQUIRED_HEADERS: dict[str, set[str]] = {
    "razorpay": {"payment_id", "order_id", "cycle", "captured_at", "settled_at",
                 "gross_amount", "fee", "gst", "net_amount", "entity_type"},
    "bank": {"txn_id", "value_date", "narration", "debit", "credit"},
    "erp": {"invoice_no", "order_id", "invoice_date", "invoice_amount"},
}


def _paise_or_zero(value: str) -> int:
    return rupees_to_paise(value) if str(value).strip() else 0


class RazorpaySettlementRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    payment_id: str
    order_id: str
    cycle: str
    captured_at: date
    settled_at: date
    gross_paise: int
    fee_paise: int
    gst_paise: int
    net_paise: int
    entity_type: str


class BankStatementRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    txn_id: str
    value_date: date
    narration: str
    debit_paise: int = 0
    credit_paise: int = 0

    @field_validator("txn_id", "narration")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("blank required field")
        return v


class ErpSalesRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    invoice_no: str
    order_id: str
    invoice_date: date
    invoice_paise: int


def build_razorpay(raw: dict[str, str]) -> RazorpaySettlementRow:
    return RazorpaySettlementRow(
        payment_id=raw["payment_id"], order_id=raw["order_id"], cycle=raw["cycle"],
        captured_at=raw["captured_at"], settled_at=raw["settled_at"],
        gross_paise=_paise_or_zero(raw["gross_amount"]),
        fee_paise=_paise_or_zero(raw["fee"]), gst_paise=_paise_or_zero(raw["gst"]),
        net_paise=_paise_or_zero(raw["net_amount"]), entity_type=raw["entity_type"],
    )


def build_bank(raw: dict[str, str]) -> BankStatementRow:
    return BankStatementRow(
        txn_id=raw["txn_id"], value_date=raw["value_date"], narration=raw["narration"],
        debit_paise=_paise_or_zero(raw.get("debit", "")),
        credit_paise=_paise_or_zero(raw.get("credit", "")),
    )


def build_erp(raw: dict[str, str]) -> ErpSalesRow:
    return ErpSalesRow(
        invoice_no=raw["invoice_no"], order_id=raw["order_id"],
        invoice_date=raw["invoice_date"],
        invoice_paise=_paise_or_zero(raw["invoice_amount"]),
    )


BUILDERS = {"razorpay": build_razorpay, "bank": build_bank, "erp": build_erp}
