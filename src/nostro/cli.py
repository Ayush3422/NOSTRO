"""Command line entry points."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from nostro.audit.ledger import Ledger
from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate
from nostro.money import paise_to_rupees
from nostro.pipeline import CloseConfig, run_close

app = typer.Typer(help="Nostro - three-way settlement reconciliation.")


@app.command("generate")
def generate_cmd(
    out: Path = typer.Option(Path("data/full"), help="Output directory."),
    cycles: int = typer.Option(30), payments: int = typer.Option(60),
) -> None:
    """Generate the adversarial synthetic dataset with ground truth."""
    ds = generate(GeneratorConfig(cycles=cycles, payments_per_cycle=payments), out)
    # Persist the holdout split alongside the data so `close` can pick it up
    # without the caller having to retype cycle ids: the split is a property
    # of the dataset, not something a human should have to remember.
    (out / "meta.json").write_text(
        json.dumps({"holdout_cycles": list(ds.holdout_cycles)}, indent=2),
        encoding="utf-8",
    )
    typer.echo(json.dumps(ds.row_counts, indent=2))
    typer.echo(f"holdout cycles: {', '.join(ds.holdout_cycles)}")


def _load_holdout_cycles(data_dir: Path) -> tuple[str, ...]:
    meta_path = data_dir / "meta.json"
    if not meta_path.exists():
        return ()
    return tuple(json.loads(meta_path.read_text(encoding="utf-8")).get("holdout_cycles", []))


@app.command("close")
def close_cmd(
    data: Path = typer.Option(Path("data/full")),
    audit: Path = typer.Option(Path("data/audit.jsonl")),
    model: bool = typer.Option(True, help="Use the exception desk model."),
) -> None:
    """Run the close and print the measured result."""
    client = None
    if model:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception as exc:                       # noqa: BLE001
            typer.echo(f"model unavailable ({exc}); continuing deterministic-only")

    result = run_close(CloseConfig(data_dir=data, audit_path=audit,
                                   holdout_cycles=_load_holdout_cycles(data),
                                   use_model=model), client=client)

    typer.echo(f"matches        {len(result.matches)}")
    typer.echo(f"auto-posted    {result.auto_posted} at tau={result.threshold.tau:.4f}")
    typer.echo(f"exceptions     {len(result.exceptions)}")
    typer.echo(f"quarantined    {result.quarantined_count}")
    typer.echo(f"parser         {result.parser_stats}")
    if result.report:
        r = result.report
        if result.holdout_razorpay_match_rate is not None:
            # The whole-population match_rate is biased upward in holdout
            # mode (bank/ERP rows carry no settlement_cycle, so they can
            # only be scoped to "rows a holdout match touched" -- see
            # CloseResult.holdout_razorpay_match_rate). Print the honestly-
            # scoped Razorpay-only rate instead, named for what it is.
            typer.echo(f"razorpay match rate (holdout)  {result.holdout_razorpay_match_rate:.4f}")
        else:
            typer.echo(f"match rate     {r.match_rate:.4f}")
        typer.echo(f"precision      {r.precision:.4f}")
        typer.echo(f"recall         {r.recall:.4f}")
        typer.echo(f"f1             {r.f1:.4f}")
        typer.echo(f"throughput     {r.rows_per_second:,.0f} rows/s")
    typer.echo(f"expected cost  Rs {paise_to_rupees(result.threshold.expected_cost_paise)}")
    typer.echo(f"degraded       {result.degraded or 'none'}")
    typer.echo(f"result hash    {result.result_hash}")


@app.command("verify")
def verify_cmd(audit: Path = typer.Option(Path("data/audit.jsonl"))) -> None:
    """Verify the audit ledger has not been tampered with."""
    ok, bad = Ledger(audit).verify()
    if ok:
        typer.echo("ledger intact")
    else:
        typer.echo(f"LEDGER BROKEN at entry {bad}")
        raise typer.Exit(code=1)


@app.command("replay")
def replay_cmd(
    data: Path = typer.Option(Path("data/full")),
    audit: Path = typer.Option(Path("data/audit.jsonl")),
) -> None:
    """Re-run the close and confirm it reproduces the recorded result hash."""
    original = Ledger(audit).result_hash()
    replay_path = audit.with_suffix(".replay")
    if replay_path.exists():
        replay_path.unlink()      # start from a clean ledger every run, not
                                   # an append onto whatever a prior replay left
    replayed = run_close(CloseConfig(data_dir=data, audit_path=replay_path,
                                     holdout_cycles=_load_holdout_cycles(data),
                                     use_model=False)).result_hash
    typer.echo(f"original {original}")
    typer.echo(f"replay   {replayed}")
    if original != replayed:
        typer.echo("REPLAY MISMATCH")
        raise typer.Exit(code=1)
    typer.echo("replay matches")


if __name__ == "__main__":
    app()
