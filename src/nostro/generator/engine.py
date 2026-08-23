"""Adversarial synthetic data generator.

Emits three CSVs shaped like what an Indian merchant finance team actually
receives, plus the ground-truth links. Every chaos injector is rate-controlled
by GeneratorConfig so the eval harness can attribute error to a cause.
"""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from nostro.generator.config import GeneratorConfig
from nostro.generator.narration import Bank, corrupt_narration, render_narration
from nostro.money import paise_to_rupees


class GeneratedDataset(BaseModel):
    razorpay_csv: Path
    bank_csv: Path
    erp_csv: Path
    ground_truth_json: Path
    holdout_cycles: tuple[str, ...]
    row_counts: dict[str, int]


@dataclass
class _Payment:
    payment_id: str
    order_id: str
    cycle: str
    captured_at: date
    settled_at: date
    gross_paise: int
    fee_paise: int
    gst_paise: int
    net_paise: int
    entity_type: str = "payment"


@dataclass
class _Link:
    link_id: str
    razorpay_ids: list[str] = field(default_factory=list)
    bank_ids: list[str] = field(default_factory=list)
    erp_ids: list[str] = field(default_factory=list)


_BASE_DATE = date(2026, 6, 1)


def _fee_split(gross_paise: int, cfg: GeneratorConfig, drift: int) -> tuple[int, int, int]:
    """Fee and GST in Decimal, floored to paise. `drift` injects the plus/minus
    one paise disagreement real gateways and ERPs exhibit against each other."""
    fee = int((Decimal(gross_paise) * cfg.fee_bps / 10000).to_integral_value())
    gst = int((Decimal(fee) * cfg.gst_bps / 10000).to_integral_value())
    fee += drift
    net = gross_paise - fee - gst
    return fee, gst, net


def generate(cfg: GeneratorConfig, out_dir: Path) -> GeneratedDataset:
    rng = random.Random(cfg.seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cycles = [f"C{i:03d}" for i in range(cfg.cycles)]
    payments: list[_Payment] = []
    links: list[_Link] = []
    bank_rows: list[dict[str, str]] = []
    erp_rows: list[dict[str, str]] = []
    used_utrs: list[str] = []
    seq = 0

    def next_id(prefix: str) -> str:
        nonlocal seq
        seq += 1
        return f"{prefix}{seq:07d}"

    # --- 1. payments and their ERP invoices -------------------------------
    for ci, cycle in enumerate(cycles):
        captured = _BASE_DATE + timedelta(days=ci)
        settled = captured + timedelta(days=cfg.settlement_lag_days)
        for _ in range(cfg.payments_per_cycle):
            gross = rng.randrange(19900, 800000)
            drift = rng.choice((-1, 1)) if rng.random() < cfg.rounding_drift_rate else 0
            fee, gst, net = _fee_split(gross, cfg, drift)
            pay = _Payment(
                payment_id=next_id("pay_"), order_id=next_id("order_"), cycle=cycle,
                captured_at=captured, settled_at=settled,
                gross_paise=gross, fee_paise=fee, gst_paise=gst, net_paise=net,
            )
            payments.append(pay)
            if rng.random() >= cfg.missing_row_rate:
                erp_id = next_id("inv_")
                erp_rows.append({
                    "invoice_no": erp_id, "order_id": pay.order_id,
                    "invoice_date": captured.isoformat(),
                    "invoice_amount": paise_to_rupees(gross),
                    "customer": f"CUST{rng.randrange(9999):04d}",
                })
                links.append(_Link(next_id("gt_"), [pay.payment_id], [], [erp_id]))

    # --- 2. group payments into bank credits ------------------------------
    by_cycle: dict[str, list[_Payment]] = {}
    for pay in payments:
        by_cycle.setdefault(pay.cycle, []).append(pay)

    for cycle in cycles:
        pool = list(by_cycle.get(cycle, []))
        rng.shuffle(pool)
        while pool:
            if rng.random() < cfg.split_settlement_rate and len(pool) >= 3:
                take = pool[: rng.randrange(2, min(6, len(pool) + 1))]
            else:
                take = pool[:1]
            pool = pool[len(take):]

            total = sum(p.net_paise for p in take)
            utr = f"UTR{rng.randrange(10**10):010d}"
            if used_utrs and rng.random() < cfg.duplicate_utr_rate:
                utr = rng.choice(used_utrs)
            used_utrs.append(utr)

            value_date = take[0].settled_at
            if rng.random() < cfg.late_credit_rate:
                value_date += timedelta(days=rng.randrange(1, 4))

            bank_id = next_id("bk_")
            narration = render_narration(rng.choice(list(Bank)), utr, None, rng)
            if rng.random() < cfg.narration_corruption_rate:
                narration = corrupt_narration(narration, rng)

            bank_rows.append({
                "txn_id": bank_id, "value_date": value_date.isoformat(),
                "narration": narration, "debit": "",
                "credit": paise_to_rupees(total), "balance": "",
            })
            links.append(_Link(next_id("gt_"), [p.payment_id for p in take], [bank_id], []))

    # --- 3. refunds and chargebacks ---------------------------------------
    for pay in list(payments):
        if pay.entity_type != "payment":
            continue

        if rng.random() < cfg.refund_rate:
            refund = _Payment(
                payment_id=next_id("rfnd_"), order_id=pay.order_id, cycle=pay.cycle,
                captured_at=pay.captured_at, settled_at=pay.settled_at,
                gross_paise=-pay.gross_paise, fee_paise=0, gst_paise=0,
                net_paise=-pay.net_paise, entity_type="refund",
            )
            payments.append(refund)
            # A netted refund never appears on the bank side at all: it is
            # silently deducted from a later credit. Those are unmatchable by
            # construction and form part of the honest exception floor.
            if rng.random() >= cfg.netted_refund_rate:
                bank_id = next_id("bk_")
                bank_rows.append({
                    "txn_id": bank_id, "value_date": pay.settled_at.isoformat(),
                    "narration": f"NEFT DR-RAZORPAY REFUND {refund.payment_id}",
                    "debit": paise_to_rupees(-refund.net_paise), "credit": "",
                    "balance": "",
                })
                links.append(_Link(next_id("gt_"), [refund.payment_id], [bank_id], []))

        if rng.random() < cfg.chargeback_rate:
            cb_id = next_id("cb_")
            bank_id = next_id("bk_")
            bank_rows.append({
                "txn_id": bank_id,
                "value_date": (pay.settled_at + timedelta(days=7)).isoformat(),
                "narration": f"CHARGEBACK DR RAZORPAY {cb_id}",
                "debit": paise_to_rupees(pay.gross_paise), "credit": "", "balance": "",
            })
            # An orphan chargeback has no forward entry on the Razorpay side.
            if rng.random() >= cfg.orphan_chargeback_rate:
                payments.append(_Payment(
                    payment_id=cb_id, order_id=pay.order_id, cycle=pay.cycle,
                    captured_at=pay.settled_at, settled_at=pay.settled_at,
                    gross_paise=-pay.gross_paise, fee_paise=0, gst_paise=0,
                    net_paise=-pay.gross_paise, entity_type="chargeback",
                ))
                links.append(_Link(next_id("gt_"), [cb_id], [bank_id], []))

    # --- 4. write files ----------------------------------------------------
    rp_path = out_dir / "razorpay_settlement.csv"
    with rp_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["payment_id", "order_id", "settlement_id", "cycle", "captured_at",
                    "settled_at", "gross_amount", "fee", "gst", "net_amount",
                    "entity_type"])
        for p in payments:
            w.writerow([p.payment_id, p.order_id, f"setl_{p.cycle}", p.cycle,
                        p.captured_at.isoformat(), p.settled_at.isoformat(),
                        paise_to_rupees(p.gross_paise), paise_to_rupees(p.fee_paise),
                        paise_to_rupees(p.gst_paise), paise_to_rupees(p.net_paise),
                        p.entity_type])

    bank_path = out_dir / "bank_statement.csv"
    with bank_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["txn_id", "value_date", "narration",
                                           "debit", "credit", "balance"])
        w.writeheader()
        w.writerows(bank_rows)

    erp_path = out_dir / "erp_sales.csv"
    with erp_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["invoice_no", "order_id", "invoice_date",
                                           "invoice_amount", "customer"])
        w.writeheader()
        w.writerows(erp_rows)

    gt_path = out_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(
        [{"link_id": ln.link_id, "razorpay_ids": ln.razorpay_ids,
          "bank_ids": ln.bank_ids, "erp_ids": ln.erp_ids} for ln in links],
        indent=2), encoding="utf-8")

    n_hold = max(1, int(len(cycles) * cfg.holdout_cycle_fraction))
    holdout = tuple(cycles[-n_hold:])

    return GeneratedDataset(
        razorpay_csv=rp_path, bank_csv=bank_path, erp_csv=erp_path,
        ground_truth_json=gt_path, holdout_cycles=holdout,
        row_counts={"razorpay": len(payments), "bank": len(bank_rows),
                    "erp": len(erp_rows), "links": len(links)},
    )
