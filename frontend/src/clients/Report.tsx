import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { useState } from "react";
import { api, qs, type Rec } from "../lib/api";
import { localToday } from "../lib/domain";
import { formatMoney } from "../lib/format";

const POSITION_REPORTS = ["balance_sheet", "aged_receivable", "aged_payable"];

export function Report({ reportKey }: { reportKey: string }) {
  const [from, setFrom] = useState(`${new Date().getFullYear()}-01-01`);
  const [to, setTo] = useState(localToday());
  const position = POSITION_REPORTS.includes(reportKey);
  const query = useQuery<any>({ queryKey: ["report", reportKey, from, to], queryFn: () => api.get(`/api/reports/${reportKey}${qs({ date_from: position ? undefined : from, date_to: to })}`) });
  const cell = (col: any, row: Rec) => {
    const v = row[col.key];
    if (v === undefined || v === null) return "";
    return col.money ? formatMoney(v) : v;
  };
  return (
    <div className="client-page">
      <div className="report-head">
        <h1 className="page-title">{query.data?.title ?? "Report"}</h1>
        <div className="report-filters no-print">
          {!position && <label className="inline-field">From <input className="input input-sm" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>}
          <label className="inline-field">{position ? "As of" : "To"} <input className="input input-sm" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className="btn btn-sm" onClick={() => window.print()}><Printer size={14} /> Print</button>
        </div>
      </div>
      {query.isLoading && <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>}
      {query.error && <div className="empty"><p>{(query.error as Error).message}</p></div>}
      {query.data && (
        <div className="card table-wrap">
          <table className="data-table report-table">
            <thead><tr>{query.data.columns.map((c: any) => <th key={c.key} className={c.money ? "num" : ""}>{c.label}</th>)}</tr></thead>
            <tbody>
              {query.data.rows.length === 0 && <tr><td colSpan={query.data.columns.length} className="muted empty-row">No posted entries in this period.</td></tr>}
              {query.data.rows.map((r: Rec, i: number) => (
                <tr key={i} className={r.section ? "report-section" : r.subtotal ? "report-subtotal" : ""}>
                  {query.data.columns.map((c: any) => <td key={c.key} className={c.money ? "num" : ""}>{cell(c, r)}</td>)}
                </tr>
              ))}
            </tbody>
            {query.data.totals && <tfoot><tr>{query.data.columns.map((c: any) => <td key={c.key} className={c.money ? "num" : ""}>{cell(c, query.data.totals)}</td>)}</tr></tfoot>}
          </table>
        </div>
      )}
    </div>
  );
}
