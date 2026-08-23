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


def _blank_paise_is_zero(value: str) -> int:
    """Bank debit/credit only: blank is the legitimate other side of a
    debit/credit pair, so blank means zero. Anything non-blank still goes
    through rupees_to_paise, so garbage like "N/A" quarantines."""
    return rupees_to_paise(value) if str(value).strip() else 0


def _required_paise(value: str) -> int:
    """Razorpay and ERP amounts are always expected to be present. Blank or
    corrupted is a contract violation, not a zero — no coercion, straight to
    rupees_to_paise so it raises and the row quarantines."""
    return rupees_to_paise(value)


def _not_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("blank required field")
    return v


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

    @field_validator("payment_id", "order_id")
    @classmethod
    def _not_blank_id(cls, v: str) -> str:
        return _not_blank(v)


class BankStatementRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    txn_id: str
    value_date: date
    narration: str
    debit_paise: int = 0
    credit_paise: int = 0

    @field_validator("txn_id", "narration")
    @classmethod
    def _not_blank_field(cls, v: str) -> str:
        return _not_blank(v)


class ErpSalesRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    invoice_no: str
    order_id: str
    invoice_date: date
    invoice_paise: int

    @field_validator("invoice_no", "order_id")
    @classmethod
    def _not_blank_id(cls, v: str) -> str:
        return _not_blank(v)


def build_razorpay(raw: dict[str, str]) -> RazorpaySettlementRow:
    return RazorpaySettlementRow(
        payment_id=raw["payment_id"], order_id=raw["order_id"], cycle=raw["cycle"],
        captured_at=raw["captured_at"], settled_at=raw["settled_at"],
        gross_paise=_required_paise(raw["gross_amount"]),
        fee_paise=_required_paise(raw["fee"]), gst_paise=_required_paise(raw["gst"]),
        net_paise=_required_paise(raw["net_amount"]), entity_type=raw["entity_type"],
    )


def build_bank(raw: dict[str, str]) -> BankStatementRow:
    return BankStatementRow(
        txn_id=raw["txn_id"], value_date=raw["value_date"], narration=raw["narration"],
        debit_paise=_blank_paise_is_zero(raw.get("debit", "")),
        credit_paise=_blank_paise_is_zero(raw.get("credit", "")),
    )


def build_erp(raw: dict[str, str]) -> ErpSalesRow:
    return ErpSalesRow(
        invoice_no=raw["invoice_no"], order_id=raw["order_id"],
        invoice_date=raw["invoice_date"],
        invoice_paise=_required_paise(raw["invoice_amount"]),
    )


BUILDERS = {"razorpay": build_razorpay, "bank": build_bank, "erp": build_erp}
