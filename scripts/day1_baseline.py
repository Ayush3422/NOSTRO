"""Day 1 baseline: deterministic matching only, measured on the full dataset."""
from pathlib import Path
from time import perf_counter

from nostro.eval.harness import evaluate, load_ground_truth
from nostro.ingest.loader import load_csv
from nostro.match.blocking import BlockingConfig, build_blocks
from nostro.match.deterministic import match_deterministic
from nostro.models import Source
from nostro.normalize.canonical import CanonicalSet, to_canonical
from nostro.normalize.narration_parser import NarrationParser

data = Path("data/full")
parser = NarrationParser()

rp = load_csv(data / "razorpay_settlement.csv", Source.RAZORPAY)
bk = load_csv(data / "bank_statement.csv", Source.BANK)
erp = load_csv(data / "erp_sales.csv", Source.ERP)

cset = CanonicalSet(
    razorpay=to_canonical(rp.rows, Source.RAZORPAY),
    bank=to_canonical(bk.rows, Source.BANK, parser),
    erp=to_canonical(erp.rows, Source.ERP),
)

started = perf_counter()
cfg = BlockingConfig()
result = match_deterministic(cset, build_blocks(cset, cfg), cfg)
elapsed = perf_counter() - started

report = evaluate(result.matches, load_ground_truth(data / "ground_truth.json"),
                  cset, elapsed)
print(f"rows          {report.rows_evaluated}")
print(f"match rate    {report.match_rate:.4f}")
print(f"precision     {report.precision:.4f}")
print(f"recall        {report.recall:.4f}")
print(f"f1            {report.f1:.4f}")
print(f"throughput    {report.rows_per_second:,.0f} rows/s")
print(f"quarantined   {len(rp.quarantined) + len(bk.quarantined) + len(erp.quarantined)}")
print(f"parser        {parser.stats}")
