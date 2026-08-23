"""Source rows to canonical rows.

Canonical rows carry an unsigned amount plus a direction, so a refund debit and
a settlement credit of the same size never accidentally match each other.
"""
from __future__ import annotations

from pydantic import BaseModel

from nostro.ingest.contracts import BankStatementRow, ErpSalesRow, RazorpaySettlementRow
from nostro.models import CanonicalRow, Direction, Source
from nostro.normalize.narration_parser import NarrationParser


def _direction(paise: int) -> tuple[Direction, int]:
    return (Direction.CREDIT, paise) if paise >= 0 else (Direction.DEBIT, -paise)


def _razorpay(row: RazorpaySettlementRow) -> CanonicalRow:
    direction, amount = _direction(row.net_paise)
    return CanonicalRow(
        source=Source.RAZORPAY, row_id=row.payment_id, amount_paise=amount,
        direction=direction, value_date=row.settled_at, settlement_cycle=row.cycle,
        refs={"payment_id": row.payment_id, "order_id": row.order_id,
              "entity_type": row.entity_type},
    )


def _bank(row: BankStatementRow, parser: NarrationParser) -> CanonicalRow:
    parsed = parser.parse(row.narration)
    net = row.credit_paise - row.debit_paise
    direction, amount = _direction(net)
    refs: dict[str, str] = {"kind": parsed.kind}
    if parsed.utr:
        refs["utr"] = parsed.utr
    if parsed.rrn:
        refs["rrn"] = parsed.rrn
    return CanonicalRow(
        source=Source.BANK, row_id=row.txn_id, amount_paise=amount,
        direction=direction, value_date=row.value_date, refs=refs,
        narration_raw=row.narration, parsed_by=parsed.parsed_by,
    )


def _erp(row: ErpSalesRow) -> CanonicalRow:
    direction, amount = _direction(row.invoice_paise)
    return CanonicalRow(
        source=Source.ERP, row_id=row.invoice_no, amount_paise=amount,
        direction=direction, value_date=row.invoice_date,
        refs={"invoice_no": row.invoice_no, "order_id": row.order_id},
    )


def to_canonical(
    rows: list[BaseModel], source: Source, parser: NarrationParser | None = None
) -> list[CanonicalRow]:
    if source is Source.RAZORPAY:
        return [_razorpay(r) for r in rows]
    if source is Source.ERP:
        return [_erp(r) for r in rows]
    active = parser or NarrationParser()
    return [_bank(r, active) for r in rows]


class CanonicalSet(BaseModel):
    razorpay: list[CanonicalRow] = []
    bank: list[CanonicalRow] = []
    erp: list[CanonicalRow] = []

    def by_id(self) -> dict[str, CanonicalRow]:
        index: dict[str, CanonicalRow] = {}
        for row in (*self.razorpay, *self.bank, *self.erp):
            existing = index.get(row.row_id)
            if existing is not None:
                raise ValueError(
                    f"row_id collision on {row.row_id!r}: "
                    f"{existing.source.value} and {row.source.value} both claim it"
                )
            index[row.row_id] = row
        return index

    @property
    def total_rows(self) -> int:
        return len(self.razorpay) + len(self.bank) + len(self.erp)
