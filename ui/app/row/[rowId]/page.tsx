// ui/app/row/[rowId]/page.tsx  — screen 3: drill-down
import Link from "next/link";
import { get, rupees } from "@/lib/api";

export const dynamic = "force-dynamic";

type Row = { row_id: string; source: string; amount_paise: number; direction: string;
             value_date: string; refs: Record<string, string>;
             narration_raw: string | null; parsed_by: string };

type MatchPayload = {
  match_id: string; method: string; residual_paise: number; probability: number;
  razorpay_ids: string[]; bank_ids: string[]; erp_ids: string[];
};

export default async function Page({ params }: { params: Promise<{ rowId: string }> }) {
  const { rowId } = await params;
  const d = await get<{ row: Row; matches: MatchPayload[]; exception: any; siblings: Row[] }>(
    `/api/close/row/${rowId}`);
  // A row can legitimately carry two matches under per-axis consumption --
  // one ERP-axis, one bank-axis. `siblings` is deduplicated across all of
  // them by the API; group rows for each match's own table by its own id set.
  const siblingsByRowId = new Map(d.siblings.map((s) => [s.row_id, s]));
  return (
    <>
      <h1>{d.row.row_id}</h1>
      <div className="grid">
        <div className="card"><div className="label">Source</div><div className="value">{d.row.source}</div></div>
        <div className="card"><div className="label">Amount</div><div className="value">{rupees(d.row.amount_paise)}</div></div>
        <div className="card"><div className="label">Direction</div><div className="value">{d.row.direction}</div></div>
        <div className="card"><div className="label">Value date</div><div className="value">{d.row.value_date}</div></div>
      </div>

      {d.row.narration_raw && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <div className="label">Narration — parsed by {d.row.parsed_by}</div>
          <p className="mono">{d.row.narration_raw}</p>
        </div>
      )}

      {d.matches.length > 0 ? (
        d.matches.map((match) => {
          const ids = [...match.razorpay_ids, ...match.bank_ids, ...match.erp_ids]
            .filter((rid) => rid !== rowId);
          return (
            <div key={match.match_id} style={{ marginBottom: "1.5rem" }}>
              <h2 style={{ fontSize: "1rem" }}>
                Matched via {match.method}, residual {rupees(match.residual_paise)},
                {" "}probability {match.probability.toFixed(4)}
              </h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Row</th><th>Source</th><th>Amount</th><th>Date</th></tr></thead>
                  <tbody>
                    {ids.map((rid) => {
                      const s = siblingsByRowId.get(rid);
                      return s ? (
                        <tr key={s.row_id}>
                          <td><Link className="row" href={`/row/${s.row_id}`}>{s.row_id}</Link></td>
                          <td>{s.source}</td><td>{rupees(s.amount_paise)}</td><td>{s.value_date}</td>
                        </tr>
                      ) : null;
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })
      ) : (
        <p className="bad">Unmatched — {d.exception?.exception_class ?? "no classification"}</p>
      )}
      {d.exception && <p style={{ color: "var(--muted)" }}>{d.exception.evidence}</p>}
    </>
  );
}
