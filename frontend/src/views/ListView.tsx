import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, qs, type ActionMeta, type FieldMeta, type ModelMeta, type Rec } from "../lib/api";
import { FieldDisplay } from "../fields/FieldInput";
import { formatValue, groupLabel } from "../lib/format";
import { useToast } from "../lib/hooks";
import { ActionDialog } from "./ActionDialog";

type Column = { field: FieldMeta; widget?: string; sum?: boolean };

const isNum = (f: FieldMeta) => ["money", "float", "int"].includes(f.type);

function columnsOf(meta: ModelMeta): Column[] {
  return (meta.views.list?.columns ?? []).map((c: any) => (typeof c === "string" ? { field: meta.fields[c] } : { field: meta.fields[c.field], widget: c.widget, sum: c.sum })).filter((c: Column) => c.field);
}

export function ListView({ meta, domain, groupBy, offset, limit, onOpen, onTotal }: { meta: ModelMeta; domain: any[]; groupBy: string | null; offset: number; limit: number; onOpen: (id: number) => void; onTotal: (t: number | null) => void }) {
  const columns = columnsOf(meta);
  const [order, setOrder] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState<ActionMeta | null>(null);
  const client = useQueryClient();
  const toast = useToast();
  const names = ["id", ...columns.map((c) => c.field.name)].join(",");

  const query = useQuery<{ records: Rec[]; total: number }>({
    queryKey: ["records", meta.name, domain, names, order, offset, limit], placeholderData: keepPreviousData, enabled: !groupBy,
    queryFn: () => api.get(`/api/models/${meta.name}/records${qs({ domain, fields: names, order, limit, offset })}`),
  });
  const sums = columns.filter((c) => c.sum);
  const totals = useQuery<{ groups: Rec[] }>({
    queryKey: ["records", meta.name, "totals", domain], enabled: sums.length > 0,
    queryFn: () => api.post(`/api/models/${meta.name}/read_group`, { domain, group_by: [], measures: sums.map((c) => c.field.name) }),
  });
  useEffect(() => onTotal(groupBy ? null : query.data?.total ?? null), [query.data?.total, groupBy]); // eslint-disable-line
  useEffect(() => setSelected([]), [domain, offset]);

  const toggleSort = (name: string) => setOrder(order === `${name} ASC` ? `${name} DESC` : `${name} ASC`);
  const headers = columns.map((c) => {
    const direction = order?.startsWith(`${c.field.name} `) ? order.split(" ")[1] : null;
    return (
      <th key={c.field.name} className={isNum(c.field) ? "num" : ""} aria-sort={direction === "ASC" ? "ascending" : direction === "DESC" ? "descending" : undefined}>
        {c.field.type === "many2many" ? c.field.label : <button className="th-btn" onClick={() => toggleSort(c.field.name)}>{c.field.label}{direction === "ASC" && <ArrowUp size={12} />}{direction === "DESC" && <ArrowDown size={12} />}</button>}
      </th>
    );
  });

  async function runBulk(action: ActionMeta, params: Rec = {}) {
    const res = await api.post(`/api/models/${meta.name}/actions/${action.key}`, { ids: selected, params });
    toast(res?.message ?? `${action.label}: done.`);
    setPending(null);
    setSelected([]);
    client.invalidateQueries({ queryKey: ["records", meta.name] });
  }

  async function removeSelected() {
    if (!window.confirm(`Delete ${selected.length} record(s)?`)) return;
    try {
      for (const id of selected) await api.del(`/api/models/${meta.name}/records/${id}`);
      toast("Deleted.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setSelected([]);
    client.invalidateQueries({ queryKey: ["records", meta.name] });
  }

  const listButtons: ActionMeta[] = (meta.views.list?.buttons ?? []).map((b: any) => meta.actions[b.action]).filter(Boolean);
  const bar = selected.length > 0 && (
    <div className="bulk-bar" role="toolbar" aria-label="Selection actions">
      <strong>{selected.length} selected</strong>
      {listButtons.map((a) => <button key={a.key} className="btn btn-sm btn-primary" onClick={() => (a.params || a.confirm ? setPending(a) : runBulk(a).catch((e) => toast(e.message, "error")))}>{a.label}</button>)}
      {meta.access.d && <button className="btn btn-sm" onClick={removeSelected}><Trash2 size={13} /> Delete</button>}
      <button className="link-btn" onClick={() => setSelected([])}>Clear</button>
    </div>
  );

  const footer = sums.length > 0 && totals.data?.groups[0] && (
    <tfoot><tr><td />{columns.map((c) => <td key={c.field.name} className="num">{c.sum ? formatValue(c.field, totals.data!.groups[0][c.field.name]) : ""}</td>)}</tr></tfoot>
  );

  if (groupBy) {
    return (
      <div className="table-wrap card">
        <table className="data-table clickable">
          <thead><tr><th className="check-col" />{headers}</tr></thead>
          <GroupedBody meta={meta} domain={domain} groupBy={groupBy} columns={columns} names={names} order={order} onOpen={onOpen} />
          {footer}
        </table>
      </div>
    );
  }
  if (query.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (query.error) return <div className="empty"><h2>This list could not be loaded</h2><p>{(query.error as Error).message}</p></div>;
  const records = query.data?.records ?? [];
  if (records.length === 0) {
    return <div className="empty"><h2>No {meta.label.toLowerCase()} records found</h2><p>{domain.length ? "Try removing a filter, or create a new record." : meta.access.c ? "Create the first one with the New button." : "Nothing has been shared with you yet."}</p></div>;
  }
  const allSelected = records.every((r) => selected.includes(r.id));
  return (
    <>
      {bar}
      <div className="table-wrap card">
        <table className="data-table clickable">
          <thead><tr><th className="check-col"><input type="checkbox" aria-label="Select all" checked={allSelected} onChange={(e) => setSelected(e.target.checked ? records.map((r) => r.id) : [])} /></th>{headers}</tr></thead>
          <tbody>{records.map((r) => <Row key={r.id} r={r} columns={columns} onOpen={onOpen} selected={selected.includes(r.id)} onSelect={(on) => setSelected(on ? [...selected, r.id] : selected.filter((i) => i !== r.id))} />)}</tbody>
          {footer}
        </table>
      </div>
      {pending && <ActionDialog action={pending} onSubmit={(p) => runBulk(pending, p)} onClose={() => setPending(null)} />}
    </>
  );
}

function Row({ r, columns, onOpen, selected, onSelect }: { r: Rec; columns: Column[]; onOpen: (id: number) => void; selected?: boolean; onSelect?: (on: boolean) => void }) {
  return (
    <tr onClick={() => onOpen(r.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.id); }}>
      <td className="check-col" onClick={(e) => e.stopPropagation()}>{onSelect && <input type="checkbox" aria-label={`Select ${r.display_name}`} checked={!!selected} onChange={(e) => onSelect(e.target.checked)} />}</td>
      {columns.map((c) => <td key={c.field.name} className={isNum(c.field) ? "num" : ""}><FieldDisplay field={c.field} value={r[c.field.name]} widget={c.widget} /></td>)}
    </tr>
  );
}

type GroupProps = { meta: ModelMeta; domain: any[]; groupBy: string; columns: Column[]; names: string; order: string | null; onOpen: (id: number) => void };

function GroupedBody(props: GroupProps) {
  const { meta, domain, groupBy, columns } = props;
  const measures = columns.filter((c) => c.sum).map((c) => c.field.name);
  const groups = useQuery<{ groups: Rec[] }>({
    queryKey: ["records", meta.name, "group", domain, groupBy], queryFn: () => api.post(`/api/models/${meta.name}/read_group`, { domain, group_by: [groupBy], measures }),
  });
  return <tbody>{groups.data?.groups.map((g) => <GroupRows key={JSON.stringify(g[groupBy])} group={g} {...props} />)}</tbody>;
}

function GroupRows({ group, meta, domain, groupBy, columns, names, order, onOpen }: GroupProps & { group: Rec }) {
  const [open, setOpen] = useState(false);
  const [name, gran] = groupBy.split(":");
  const key = group[groupBy];
  let groupDomain: any[];
  if (key && meta.fields[name]?.type.startsWith("date")) {
    const unit = gran || "month";
    const start = new Date(`${key}T12:00:00Z`);
    const end = new Date(start);
    if (unit === "day") end.setUTCDate(end.getUTCDate() + 1);
    if (unit === "week") end.setUTCDate(end.getUTCDate() + 7);
    if (unit === "month") end.setUTCMonth(end.getUTCMonth() + 1);
    if (unit === "quarter") end.setUTCMonth(end.getUTCMonth() + 3);
    if (unit === "year") end.setUTCFullYear(end.getUTCFullYear() + 1);
    groupDomain = [[name, ">=", key], [name, "<", end.toISOString().slice(0, 10)]];
  } else {
    groupDomain = [[name, "=", Array.isArray(key) ? key[0] : key ?? false]];
  }
  const rows = useQuery<{ records: Rec[] }>({
    queryKey: ["records", meta.name, "group-rows", domain, groupBy, key, order], enabled: open,
    queryFn: () => api.get(`/api/models/${meta.name}/records${qs({ domain: [...domain, ...groupDomain], fields: names, order, limit: 200 })}`),
  });
  return (
    <>
      <tr className="group-row" onClick={() => setOpen(!open)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setOpen(!open); }} aria-expanded={open}>
        <td className="check-col">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
        {columns.map((c, i) => <td key={c.field.name} className={isNum(c.field) && i > 0 ? "num" : ""}>{i === 0 ? `${groupLabel(key, groupBy)} (${group.__count})` : c.sum ? formatValue(c.field, group[c.field.name]) : ""}</td>)}
      </tr>
      {open && rows.data?.records.map((r) => <Row key={r.id} r={r} columns={columns} onOpen={onOpen} />)}
    </>
  );
}
