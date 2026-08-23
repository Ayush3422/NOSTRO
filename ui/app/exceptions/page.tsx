// ui/app/exceptions/page.tsx  — screen 2: the honest list
import Link from "next/link";
import { get, rupees, type ExceptionItem } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const data = await get<{ total: number; items: ExceptionItem[] }>(
    "/api/close/exceptions?limit=200");
  return (
    <>
      <h1>Exceptions — {data.total.toLocaleString("en-IN")} unresolved</h1>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Class</th><th>Amount</th><th>Rows</th><th>Evidence</th></tr>
          </thead>
          <tbody>
            {data.items.map((e) => (
              <tr key={e.exception_id}>
                <td><span className="badge">{e.exception_class}</span></td>
                <td>{rupees(e.amount_paise)}</td>
                <td>
                  {e.row_ids.map((id) => (
                    <Link className="row" key={id} href={`/row/${id}`}>{id} </Link>
                  ))}
                </td>
                <td style={{ color: "var(--muted)" }}>{e.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
