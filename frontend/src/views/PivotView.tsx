import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ModelMeta, type Rec } from "../lib/api";
import { formatMoney, formatQty, groupLabel } from "../lib/format";

export function PivotView({ meta, domain, groupBy }: { meta: ModelMeta; domain: any[]; groupBy: string | null }) {
  const spec = meta.views.pivot;
  const row: string = groupBy ?? spec.row;
  const col: string | null = spec.col && spec.col !== row ? spec.col : null;
  const measures: string[] = (spec.measures ?? []).filter((m: string) => meta.fields[m]);
  const [measure, setMeasure] = useState(measures[0] ?? "__count");
  const query = useQuery<{ groups: Rec[] }>({
    queryKey: ["records", meta.name, "pivot", domain, row, col, measure],
    queryFn: () => api.post(`/api/models/${meta.name}/read_group`, { domain, group_by: col ? [row, col] : [row], measures: measure === "__count" ? [] : [measure] }),
  });
  if (query.error) return <div className="empty"><h2>This pivot could not be loaded</h2><p>{(query.error as Error).message}</p></div>;
  const money = measure !== "__count" && meta.fields[measure]?.type === "money";
  const fmt = (v: number) => (money ? formatMoney(v) : formatQty(v));
  const groups = query.data?.groups ?? [];
  const value = (g: Rec) => (measure === "__count" ? Number(g.__count) : Number(g[measure] ?? 0));
  const rows = Array.from(new Set(groups.map((g) => groupLabel(g[row], row))));
  const cols = col ? Array.from(new Set(groups.map((g) => groupLabel(g[col], col)))) : [];
  const cell = (r: string, c?: string) => groups.filter((g) => groupLabel(g[row], row) === r && (c === undefined || groupLabel(g[col!], col!) === c)).reduce((s, g) => s + value(g), 0);
  const colTotal = (c: string) => groups.filter((g) => groupLabel(g[col!], col!) === c).reduce((s, g) => s + value(g), 0);
  const grand = groups.reduce((s, g) => s + value(g), 0);
  return (
    <div className="card graph-card">
      <div className="graph-toolbar">
        <label className="inline-field">Measure
          <select className="input input-sm" value={measure} onChange={(e) => setMeasure(e.target.value)}>
            {measures.map((m) => <option key={m} value={m}>{meta.fields[m].label}</option>)}
            <option value="__count">Count</option>
          </select>
        </label>
      </div>
      {query.isLoading ? <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>
        : rows.length === 0 ? <div className="empty"><p>No data for this selection.</p></div> : (
        <div className="table-wrap">
          <table className="data-table pivot">
            <thead><tr><th>{meta.fields[row.split(":")[0]]?.label}</th>{cols.map((c) => <th key={c} className="num">{c}</th>)}<th className="num">Total</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r}><th scope="row">{r}</th>{cols.map((c) => <td key={c} className="num">{cell(r, c) ? fmt(cell(r, c)) : ""}</td>)}<td className="num strong">{fmt(cell(r))}</td></tr>)}</tbody>
            <tfoot><tr><th scope="row">Total</th>{cols.map((c) => <td key={c} className="num">{fmt(colTotal(c))}</td>)}<td className="num strong">{fmt(grand)}</td></tr></tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
