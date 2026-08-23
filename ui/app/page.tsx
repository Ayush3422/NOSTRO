// ui/app/page.tsx  — screen 1: the close
import { get, pct, rupees, type Summary } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const s = await get<Summary>("/api/close/summary");
  const isHoldout = s.evaluation_mode === "holdout";

  const matchRateLabel = isHoldout
    ? "Match rate (holdout, Razorpay-side)"
    : "Match rate";
  const matchRateValue = isHoldout
    ? pct(s.holdout_razorpay_match_rate)
    : pct(s.match_rate);

  const cards = [
    [matchRateLabel, matchRateValue],
    ["Precision", pct(s.precision)],
    ["Recall", pct(s.recall)],
    ["F1", s.f1 === null || s.f1 === undefined ? "—" : s.f1.toFixed(4)],
    ["Rows", s.rows.toLocaleString("en-IN")],
    ["Throughput", s.rows_per_second ? `${Math.round(s.rows_per_second).toLocaleString("en-IN")}/s` : "—"],
    ["Auto-posted", `${s.auto_posted} at τ=${s.tau.toFixed(3)}`],
    ["Exceptions", s.exceptions.toLocaleString("en-IN")],
  ];

  return (
    <>
      <h1>
        Close summary
        <span className={`mode-badge mode-${s.evaluation_mode}`}>
          {isHoldout ? "Holdout evaluation" : s.evaluation_mode}
        </span>
      </h1>
      {isHoldout && (
        <p style={{ color: "var(--muted)", marginTop: "-0.75rem" }}>
          Metrics below are measured on held-out settlement cycles the model did
          not see during calibration. The Razorpay-side match rate shown is not
          a whole-population figure — the unbiased whole-population rate is not
          available in this mode.
        </p>
      )}
      <div className="grid">
        {cards.map(([label, value]) => (
          <div className="card" key={label as string}>
            <div className="label">{label}</div>
            <div className="value">{value}</div>
          </div>
        ))}
      </div>
      <div className="card">
        <div className="label">Expected cost at τ</div>
        <div className="value">{rupees(s.expected_cost_paise)}</div>
        <p style={{ color: "var(--muted)", marginBottom: 0 }}>
          τ minimises wrong-post cost plus review cost. Both inputs are stated
          assumptions, not measurements.
        </p>
      </div>
      <p style={{ marginTop: "1.25rem" }}>
        {s.degraded.length > 0
          ? <span className="warn">Ran degraded: {s.degraded.join(", ")}. Deterministic results are unaffected.</span>
          : <span>All capabilities available.</span>}
      </p>
      <p className="mono">result hash {s.result_hash}</p>
      <p className="mono">
        narration: {s.parser_stats.regex_hits} by regex, {s.parser_stats.llm_calls} by model,{" "}
        {s.parser_stats.misses} unparsed
      </p>
    </>
  );
}
