"""Produce the chaos table for EVALUATION.md and the pitch video."""
from pathlib import Path
from tempfile import TemporaryDirectory

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.pipeline import CloseConfig, run_close


class _ExplodingClient:
    class _Messages:
        def parse(self, **kwargs):
            raise TimeoutError("model unavailable")
    messages = _Messages()


SCENARIOS = {
    "baseline": dict(),
    "narration destroyed": dict(narration_corruption_rate=0.9),
    "heavy splits": dict(split_settlement_rate=0.9),
    "duplicate UTRs": dict(duplicate_utr_rate=0.5),
    "everything at once": dict(narration_corruption_rate=0.9, split_settlement_rate=0.9,
                               duplicate_utr_rate=0.5, late_credit_rate=0.4,
                               rounding_drift_rate=0.5),
}

print("| scenario | match rate | precision | recall | exceptions | degraded |")
print("|---|---|---|---|---|---|")
for name, overrides in SCENARIOS.items():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ds = generate(GeneratorConfig(cycles=20, payments_per_cycle=40, **overrides),
                      root / "data")
        result = run_close(
            CloseConfig(data_dir=ds.razorpay_csv.parent, audit_path=root / "a.jsonl",
                        use_model=True),
            client=_ExplodingClient() if name == "baseline" else None,
        )
        r = result.report
        print(f"| {name} | {r.match_rate:.2%} | {r.precision:.2%} | {r.recall:.2%} | "
              f"{len(result.exceptions)} | {', '.join(result.degraded) or 'none'} |")
