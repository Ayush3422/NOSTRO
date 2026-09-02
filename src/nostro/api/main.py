"""Read-only HTTP surface over one cached close.

No endpoint mutates anything. Approving a resolution is out of scope for this
build and the README says so plainly rather than shipping a button that lies.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from nostro.audit.ledger import Ledger
from nostro.ingest.loader import load_csv
from nostro.models import Source
from nostro.normalize.canonical import CanonicalSet, to_canonical
from nostro.normalize.narration_parser import NarrationParser
from nostro.pipeline import CloseConfig, CloseResult, run_close


def _load_holdout_cycles(data_dir: Path) -> tuple[str, ...]:
    """Same resolution the CLI uses (`nostro.cli._load_holdout_cycles`): read the
    split that `generate` persisted alongside the data, so the API evaluates in
    the same mode a human running `nostro close` on this data_dir would see."""
    meta_path = data_dir / "meta.json"
    if not meta_path.exists():
        return ()
    return tuple(json.loads(meta_path.read_text(encoding="utf-8")).get("holdout_cycles", []))


def build_app(
    data_dir: Path = Path("data/full"),
    audit_path: Path = Path("data/audit.jsonl"),
    use_model: bool = False,
) -> FastAPI:
    app = FastAPI(title="Nostro", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:3000"],
        allow_methods=["GET"], allow_headers=["*"],
    )

    state: dict[str, object] = {}

    def snapshot() -> tuple[CloseResult, CanonicalSet]:
        if "result" not in state:
            client = None
            if use_model:
                try:
                    import anthropic
                    client = anthropic.Anthropic()
                except Exception:                       # noqa: BLE001
                    client = None
            state["result"] = run_close(
                CloseConfig(data_dir=data_dir, audit_path=audit_path,
                            holdout_cycles=_load_holdout_cycles(data_dir),
                            use_model=use_model), client=client)
            parser = NarrationParser()
            state["cset"] = CanonicalSet(
                razorpay=to_canonical(
                    load_csv(data_dir / "razorpay_settlement.csv", Source.RAZORPAY).rows,
                    Source.RAZORPAY),
                bank=to_canonical(
                    load_csv(data_dir / "bank_statement.csv", Source.BANK).rows,
                    Source.BANK, parser),
                erp=to_canonical(
                    load_csv(data_dir / "erp_sales.csv", Source.ERP).rows, Source.ERP),
            )
        return state["result"], state["cset"]           # type: ignore[return-value]

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/close/summary")
    def summary() -> dict:
        result, cset = snapshot()
        report = result.report
        # `report.match_rate` is biased upward in holdout mode: `_restrict_to_holdout`
        # builds its bank/ERP population as exactly the rows the holdout matches
        # touched, so the numerator and denominator are tautologically equal on
        # those two sources (see CloseResult.holdout_razorpay_match_rate docstring
        # in nostro/pipeline.py). It is never surfaced under `match_rate` when a
        # holdout split is in play -- only the honestly-scoped
        # `holdout_razorpay_match_rate` is, and `evaluation_mode` says which
        # regime produced the numbers so a consumer can tell them apart.
        holdout_rate = result.holdout_razorpay_match_rate
        evaluation_mode = "holdout" if holdout_rate is not None else "in_sample"
        match_rate = None if evaluation_mode == "holdout" else (report.match_rate if report else None)
        return {
            "rows": cset.total_rows,
            "matches": len(result.matches),
            "exceptions": len(result.exceptions),
            "quarantined": result.quarantined_count,
            "auto_posted": result.auto_posted,
            "tau": result.threshold.tau,
            "expected_cost_paise": result.threshold.expected_cost_paise,
            "evaluation_mode": evaluation_mode,
            "match_rate": match_rate,
            "holdout_razorpay_match_rate": holdout_rate,
            "precision": report.precision if report else None,
            "recall": report.recall if report else None,
            "f1": report.f1 if report else None,
            "rows_per_second": report.rows_per_second if report else None,
            "parser_stats": result.parser_stats,
            "degraded": result.degraded,
            "result_hash": result.result_hash,
        }

    @app.get("/api/close/matches")
    def matches(
        method: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        result, _ = snapshot()
        rows = [
            {**m.model_dump(mode="json"), "probability": p}
            for m, p in zip(result.matches, result.probabilities)
            if method is None or m.method.value == method
        ]
        return {"total": len(rows), "items": rows[offset: offset + limit]}

    @app.get("/api/close/exceptions")
    def exceptions(
        exception_class: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        result, _ = snapshot()
        rows = [
            item.model_dump(mode="json") for item in result.exceptions
            if exception_class is None or item.exception_class.value == exception_class
        ]
        return {"total": len(rows), "items": rows[offset: offset + limit]}

    @app.get("/api/close/row/{row_id}")
    def row(row_id: str) -> dict:
        result, cset = snapshot()
        found = cset.by_id().get(row_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"unknown row {row_id}")
        # A row can legitimately appear in TWO matches under per-axis
        # consumption -- one ERP-axis match and one bank-axis match for the
        # same Razorpay payment -- because the deterministic pass runs each
        # axis with its own consumed-row set. Taking only the first match
        # (via next()/enumerate()) silently hid the bank-axis leg on every
        # such row, since the ERP-axis pass always runs first. Return every
        # match touching this row, not just one. See R45.
        index = cset.by_id()
        match_payloads = []
        all_siblings: dict[str, dict] = {}
        for i, m in enumerate(result.matches):
            if row_id not in (*m.razorpay_ids, *m.bank_ids, *m.erp_ids):
                continue
            # `Match.probability` defaults to 0.0 and is never populated by
            # run_close -- the real calibrated value lives in the parallel
            # `result.probabilities` list, aligned by index to `result.matches`.
            # Inject it the same way `matches()` does, so the two endpoints
            # never disagree about a match's confidence.
            match_payloads.append({
                **m.model_dump(mode="json"),
                "probability": result.probabilities[i],
            })
            for rid in (*m.razorpay_ids, *m.bank_ids, *m.erp_ids):
                if rid in index and rid != row_id and rid not in all_siblings:
                    all_siblings[rid] = index[rid].model_dump(mode="json")
        item = next((e for e in result.exceptions if row_id in e.row_ids), None)
        siblings = list(all_siblings.values())
        return {
            "row": found.model_dump(mode="json"),
            "matches": match_payloads,
            # Deprecated alias: the first match, or null. Kept only so an
            # older client reading a single `match` field does not break
            # outright; new callers should read `matches`.
            "match": match_payloads[0] if match_payloads else None,
            "exception": item.model_dump(mode="json") if item else None,
            "siblings": siblings,
        }

    @app.get("/api/close/threshold")
    def threshold() -> dict:
        result, _ = snapshot()
        return result.threshold.model_dump(mode="json")

    @app.get("/api/close/audit")
    def audit(limit: int = Query(50, ge=1, le=500)) -> dict:
        ledger = Ledger(audit_path)
        ok, bad = ledger.verify()
        entries = ledger.entries()
        return {
            "intact": ok, "first_bad_seq": bad, "total": len(entries),
            "entries": [e.model_dump(mode="json") for e in entries[-limit:]],
        }

    return app


app = build_app()
