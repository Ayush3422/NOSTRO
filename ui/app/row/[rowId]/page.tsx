// ui/app/row/[rowId]/page.tsx  — screen 3: drill-down
import Link from "next/link";
import { get, rupees } from "@/lib/api";

export const dynamic = "force-dynamic";

type Row = { row_id: string; source: string; amount_paise: number; direction: string;
             value_date: string; refs: Record<string, string>;
             narration_raw: string | null; parsed_by: string };

export default async function Page({ params }: { params: Promise<{ rowId: string }> }) {
  const { rowId } = await params;
  const d = await get<{ row: Row; match: any; exception: any; siblings: Row[] }>(
    `/api/close/row/${rowId}`);
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

      {d.match ? (
        <>
          <h2 style={{ fontSize: "1rem" }}>
            Matched via {d.match.method}, residual {rupees(d.match.residual_paise)}
          </h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Row</th><th>Source</th><th>Amount</th><th>Date</th></tr></thead>
              <tbody>
                {d.siblings.map((s) => (
                  <tr key={s.row_id}>
                    <td><Link className="row" href={`/row/${s.row_id}`}>{s.row_id}</Link></td>
                    <td>{s.source}</td><td>{rupees(s.amount_paise)}</td><td>{s.value_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="bad">Unmatched — {d.exception?.exception_class ?? "no classification"}</p>
      )}
      {d.exception && <p style={{ color: "var(--muted)" }}>{d.exception.evidence}</p>}
    </>
  );
}
