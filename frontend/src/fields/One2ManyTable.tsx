import { Plus, Trash2 } from "lucide-react";
import { useRef } from "react";
import { api, type FieldMeta, type Rec } from "../lib/api";
import { useModelMeta, useToast } from "../lib/hooks";
import { formatMoney } from "../lib/format";
import { FieldDisplay, FieldInput } from "./FieldInput";

export type LineRow = { key: string; id?: number; values: Rec; changed: Rec };

export function linesFromRecords(records: Rec[] | undefined): LineRow[] {
  return (records ?? []).map((r) => ({ key: `r${r.id}`, id: r.id, values: r, changed: {} }));
}

let nextKey = 0;

export function One2ManyTable({ field, rows, removed, onChange, readonly, parent }: { field: FieldMeta; rows: LineRow[]; removed: number[]; onChange: (rows: LineRow[], removed: number[]) => void; readonly: boolean; parent: Rec }) {
  const meta = useModelMeta(field.comodel);
  const toast = useToast();
  // Onchange replies arrive later: they must merge into the rows as they are by then.
  const latest = useRef(rows);
  latest.current = rows;
  if (!meta.data) return <div className="muted pad">{meta.isError ? "You cannot view these lines." : "Loading..."}</div>;
  const columns = (field.columns ?? []).map((c) => meta.data.fields[c]).filter(Boolean) as FieldMeta[];
  const editable = !readonly && meta.data.access.w;
  const money = columns.filter((c) => c.name === "price_subtotal" || c.name === "debit" || c.name === "credit");

  async function addLine() {
    try {
      const defaults = await api.get(`/api/models/${field.comodel}/defaults`);
      const writable = Object.fromEntries(Object.entries(defaults).filter(([k]) => !meta.data!.fields[k]?.readonly));
      onChange([...rows, { key: `n${++nextKey}`, values: defaults as Rec, changed: writable }], removed);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function setCell(row: LineRow, name: string, value: any) {
    const col = meta.data!.fields[name];
    const apply = (extra: Rec) => {
      onChange(rows.map((r) => (r.key === row.key ? { ...r, values: { ...r.values, [name]: value, ...extra }, changed: { ...r.changed, [name]: value, ...extra } } : r)), removed);
    };
    apply({});
    if (col?.onchange) await refresh(row.key, name, { ...row.values, [name]: value });
  }

  /** Asks the server for dependent values. If the user edited the line meanwhile, asks again. */
  async function refresh(key: string, changed: string, sent: Rec) {
    try {
      const res = await api.post(`/api/models/${field.comodel}/onchange`, { field: changed, values: sent, parent: { move_type: parent.move_type } });
      const writable = Object.fromEntries(Object.entries(res).filter(([k]) => !meta.data!.fields[k]?.readonly));
      const row = latest.current.find((r) => r.key === key);
      if (!row) return;
      const merged = { ...row.values, ...res };
      latest.current = latest.current.map((r) => (r.key === key ? { ...r, values: merged, changed: { ...r.changed, ...writable } } : r));
      onChange(latest.current, removed);
      const used: Rec = { ...sent, ...res };
      if (columns.some((c) => c.onchange && JSON.stringify(used[c.name]) !== JSON.stringify(merged[c.name]))) await refresh(key, "", merged);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  return (
    <div className="table-wrap">
      <table className="data-table lines-table">
        <thead>
          <tr>
            {columns.map((c) => <th key={c.name} className={["money", "float", "int"].includes(c.type) ? "num" : ""}>{c.label}</th>)}
            {editable && <th aria-label="Actions" />}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={columns.length + 1} className="muted empty-row">No lines yet.</td></tr>}
          {rows.map((row) => (
            <tr key={row.key}>
              {columns.map((c) => (
                <td key={c.name} className={["money", "float", "int"].includes(c.type) ? "num" : ""}>
                  {editable && !c.readonly ? (
                    <FieldInput field={c} value={row.values[c.name]} compact onChange={(v) => setCell(row, c.name, v)} />
                  ) : <FieldDisplay field={c} value={row.values[c.name]} />}
                </td>
              ))}
              {editable && (
                <td className="row-actions"><button type="button" className="icon-btn" aria-label="Delete line" onClick={() => onChange(rows.filter((r) => r.key !== row.key), row.id ? [...removed, row.id] : removed)}><Trash2 size={15} /></button></td>
              )}
            </tr>
          ))}
        </tbody>
        {money.length > 0 && rows.length > 0 && (
          <tfoot><tr>{columns.map((c) => <td key={c.name} className="num">{money.includes(c) ? formatMoney(rows.reduce((s, r) => s + (Number(r.values[c.name]) || 0), 0)) : ""}</td>)}{editable && <td />}</tr></tfoot>
        )}
      </table>
      {editable && meta.data.access.c && <button type="button" className="btn btn-sm add-line" onClick={addLine}><Plus size={14} /> Add a line</button>}
    </div>
  );
}
