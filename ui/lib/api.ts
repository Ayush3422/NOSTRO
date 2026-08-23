const BASE = process.env.NEXT_PUBLIC_API || "http://127.0.0.1:8000";

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export const rupees = (paise: number) =>
  `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

export const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(2)}%`;

// NOTE: this type reflects the live /api/close/summary response, not the
// stale shape in the original task brief. In holdout mode, `match_rate` is
// null on purpose (the whole-population figure is biased); the honest
// number to show is `holdout_razorpay_match_rate`.
export type Summary = {
  rows: number; matches: number; exceptions: number; quarantined: number;
  auto_posted: number; tau: number; expected_cost_paise: number;
  evaluation_mode: "holdout" | "in_sample" | string;
  match_rate: number | null;
  holdout_razorpay_match_rate: number | null;
  precision: number | null; recall: number | null;
  f1: number | null; rows_per_second: number | null;
  parser_stats: Record<string, number>; degraded: string[]; result_hash: string;
};

export type ExceptionItem = {
  exception_id: string; row_ids: string[]; exception_class: string;
  amount_paise: number; evidence: string;
};
