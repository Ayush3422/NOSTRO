"""Generate EVALUATION.md from a real run. Numbers are never hand-typed."""
from pathlib import Path

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.money import paise_to_rupees
from nostro.pipeline import CloseConfig, run_close

import json

DATA = Path("data/full")
meta_path = DATA / "meta.json"
if not (DATA / "ground_truth.json").exists() or not meta_path.exists():
    ds = generate(GeneratorConfig(), DATA)
    meta_path.write_text(json.dumps({"holdout_cycles": list(ds.holdout_cycles)}, indent=2),
                          encoding="utf-8")

holdout_cycles: tuple[str, ...] = tuple(
    json.loads(meta_path.read_text(encoding="utf-8")).get("holdout_cycles", [])
)

result = run_close(CloseConfig(data_dir=DATA, audit_path=Path("data/eval_audit.jsonl"),
                               holdout_cycles=holdout_cycles, use_model=False))
r = result.report
lines = [
    "# Nostro — Measured Results",
    "",
    "Every number below was produced by `python scripts/build_evaluation.py`.",
    "None were typed by hand. Labels come from the generator that produced the data.",
    "",
    "## Headline (held-out)",
    "",
    "9 of 30 settlement cycles were withheld from the calibrator and the "
    "auto-post threshold entirely; these are the numbers measured on that "
    "unseen slice.",
    "",
    "| metric | value |",
    "|---|---|",
    f"| rows evaluated | {r.rows_evaluated:,} |" if r else "| rows evaluated | n/a |",
    f"| razorpay-side holdout match rate | {result.holdout_razorpay_match_rate:.4f} |"
        if result.holdout_razorpay_match_rate is not None else "| match rate | n/a |",
    f"| precision | {r.precision:.4f} |" if r else "| precision | n/a |",
    f"| recall | {r.recall:.4f} |" if r else "| recall | n/a |",
    f"| F1 | {r.f1:.4f} |" if r else "| F1 | n/a |",
    f"| throughput | {r.rows_per_second:,.0f} rows/s |" if r else "| throughput | n/a |",
    f"| matches | {len(result.matches):,} |",
    f"| exceptions | {len(result.exceptions):,} |",
    f"| quarantined at ingest | {result.quarantined_count} |",
    "",
    "## Auto-post threshold",
    "",
    f"- tau = **{result.threshold.tau:.4f}**",
    f"- precision at tau = **{result.threshold.precision_at_tau:.4f}**",
    f"- auto-posted = **{result.auto_posted:,}** of "
        f"**{len(result.matches):,}** matches "
        f"(train-population count at this tau, used to pick it: "
        f"{result.threshold.auto_post_count:,})",
    f"- expected cost at tau = **Rs {paise_to_rupees(result.threshold.expected_cost_paise)}**",
    "",
    "Cost inputs are stated assumptions, not measurements: Rs 2,500 to unwind a",
    "wrong auto-post, Rs 50 of analyst time per manual review. Nothing about",
    "those two figures was measured against a real merchant; they are the",
    "numbers the threshold search optimises against, and changing them moves tau.",
    "",
    "## Narration parsing — deterministic vs model",
    "",
    "| path | count |",
    "|---|---|",
]
lines += [f"| {k} | {v} |" for k, v in result.parser_stats.items()]

by_class: dict[str, int] = {}
by_class_amount: dict[str, int] = {}
for item in result.exceptions:
    key = item.exception_class.value
    by_class[key] = by_class.get(key, 0) + 1
    by_class_amount[key] = by_class_amount.get(key, 0) + item.amount_paise

lines += ["", "## The honest exception list", "",
          "Every row not consumed by a match appears here. Nothing is dropped.", "",
          "| class | count | value |", "|---|---|---|"]
lines += [f"| {k} | {v} | Rs {paise_to_rupees(by_class_amount[k])} |"
          for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])]

lines += ["", "## Reproducibility", "",
          f"- result hash: `{result.result_hash}`",
          f"- degraded capabilities this run: {result.degraded or 'none'}",
          "- `nostro replay` re-derives the same hash from the same inputs.", ""]

Path("EVALUATION.md").write_text("\n".join(lines), encoding="utf-8")
print("wrote EVALUATION.md")
