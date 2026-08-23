"""Generator configuration. Every chaos injector is an independently
tunable rate so the eval harness can attribute failures to a specific
kind of real-world mess rather than to 'the data was hard'."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratorConfig(BaseModel):
    seed: int = 20260823
    cycles: int = 30
    payments_per_cycle: int = 75

    fee_bps: int = 200          # 2.00% platform fee
    gst_bps: int = 1800         # 18% GST on the fee
    settlement_lag_days: int = 2

    # --- chaos injectors, all on by default ---
    split_settlement_rate: float = Field(0.35, ge=0.0, le=1.0)
    refund_rate: float = Field(0.08, ge=0.0, le=1.0)
    netted_refund_rate: float = Field(0.50, ge=0.0, le=1.0)
    chargeback_rate: float = Field(0.02, ge=0.0, le=1.0)
    orphan_chargeback_rate: float = Field(0.30, ge=0.0, le=1.0)
    duplicate_utr_rate: float = Field(0.03, ge=0.0, le=1.0)
    narration_corruption_rate: float = Field(0.15, ge=0.0, le=1.0)
    late_credit_rate: float = Field(0.05, ge=0.0, le=1.0)
    rounding_drift_rate: float = Field(0.12, ge=0.0, le=1.0)
    missing_row_rate: float = Field(0.02, ge=0.0, le=1.0)

    holdout_cycle_fraction: float = Field(0.30, ge=0.0, le=1.0)
