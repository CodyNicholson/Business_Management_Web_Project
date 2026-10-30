import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, qs, type ModelMeta, type Rec } from "../lib/api";
import { Badge, Priority } from "../fields/FieldInput";
import { formatDate, formatMoney, formatValue, initials } from "../lib/format";
import { useToast } from "../lib/hooks";

export function KanbanView({ meta, domain, groupBy, onOpen }: { meta: ModelMeta; domain: any[]; groupBy: string | null; onOpen: (id: number) => void }) {
  const spec = meta.views.kanban ?? {};
  const group: string | null = groupBy?.split(":")[0] ?? spec.group_by ?? null;
  const groupField = group ? meta.fields[group] : null;
  const fieldNames = Array.from(new Set(["id", spec.title, spec.subtitle, spec.badge, spec.amount, spec.user, spec.priority, spec.date, group, ...(spec.fields ?? [])].filter((n) => n && meta.fields[n])));
  const client = useQueryClient();
  const toast = useToast();
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const records = useQuery<{ records: Rec[]; total: number }>({
    queryKey: ["records", meta.name, "kanban", domain, group],
    queryFn: () => api.get(`/api/models/${meta.name}/records${qs({ domain, fields: fieldNames.join(","), limit: 400 })}`),
  });
  const columns = useQuery<[any, string][]>({
    queryKey: ["group-values", meta.name, group], enabled: !!groupField && (groupField.type === "many2one" || groupField.type === "selection"),
    queryFn: () => api.get(`/api/models/${meta.name}/group_values${qs({ field: group })}`),
  });
  const canDrag = !!groupField && !groupField.readonly && meta.access.w && (groupField.type === "many2one" || groupField.type === "selection");

  if (records.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (records.error) return <div className="empty"><h2>This board could not be loaded</h2><p>{(records.error as Error).message}</p></div>;
  const rows = records.data?.records ?? [];

  async function moveTo(value: any) {
    if (dragging === null) return;
    const id = dragging;
    setDragging(null);
    setOver(null);
    try {
      await api.patch(`/api/models/${meta.name}/records/${id}`, { [group!]: value });
    } catch (e) {
      toast((e as Error).message, "error");
    }
    client.invalidateQueries({ queryKey: ["records", meta.name] });
  }

  const card = (r: Rec) => <Card key={r.id} r={r} meta={meta} spec={spec} onOpen={onOpen} draggable={canDrag} onDragStart={() => setDragging(r.id)} onDragEnd={() => { setDragging(null); setOver(null); }} />;

  if (!group || !groupField) {
    if (rows.length === 0) return <div className="empty"><h2>No {meta.label.toLowerCase()} records found</h2></div>;
    return <div className="kanban-grid">{rows.map(card)}</div>;
  }
  const keyOf = (r: Rec) => { const v = r[group]; return Array.isArray(v) ? v[0] : v; };
  const cols: [any, string][] = columns.data?.length ? columns.data : Array.from(new Map(rows.map((r) => [keyOf(r), formatValue(groupField, r[group]) || "None"])).entries());
  const extra = rows.filter((r) => !cols.some((c) => c[0] === keyOf(r)));
  return (
    <div className="kanban-board">
      {cols.map(([key, label]) => {
        const items = rows.filter((r) => keyOf(r) === key);
        const sum = spec.amount ? items.reduce((s, r) => s + (r[spec.amount] ?? 0), 0) : null;
        return (
          <section key={String(key)} className={`kanban-col ${over === String(key) ? "drop-target" : ""}`} aria-label={label}
            onDragOver={(e) => { if (canDrag && dragging !== null) { e.preventDefault(); setOver(String(key)); } }}
            onDragLeave={() => setOver((o) => (o === String(key) ? null : o))} onDrop={(e) => { e.preventDefault(); moveTo(key); }}>
            <header><h2>{label}</h2><span className="muted">{items.length}</span>{sum !== null && <span className="kanban-sum">{formatMoney(sum)}</span>}</header>
            <div className="kanban-cards">{items.map(card)}</div>
          </section>
        );
      })}
      {extra.length > 0 && <section className="kanban-col" aria-label="Other"><header><h2>None</h2><span className="muted">{extra.length}</span></header><div className="kanban-cards">{extra.map(card)}</div></section>}
    </div>
  );
}

function Card({ r, meta, spec, onOpen, draggable, onDragStart, onDragEnd }: { r: Rec; meta: ModelMeta; spec: Rec; onOpen: (id: number) => void; draggable: boolean; onDragStart: () => void; onDragEnd: () => void }) {
  const f = meta.fields;
  const title = spec.title && f[spec.title] ? formatValue(f[spec.title], r[spec.title]) : r.display_name;
  return (
    <article className="kanban-card" tabIndex={0} role="link" draggable={draggable} onDragStart={onDragStart} onDragEnd={onDragEnd} onClick={() => onOpen(r.id)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.id); }}>
      <div className="kc-title">{title}</div>
      {spec.subtitle && f[spec.subtitle] && r[spec.subtitle] && <div className="kc-sub">{formatValue(f[spec.subtitle], r[spec.subtitle])}</div>}
      {(spec.fields ?? []).filter((n: string) => f[n] && r[n] !== null && r[n] !== "").map((n: string) => <div key={n} className="kc-line"><span className="muted">{f[n].label}:</span> {formatValue(f[n], r[n])}</div>)}
      {spec.amount && f[spec.amount] && <div className="kc-amount">{formatMoney(r[spec.amount])}</div>}
      <div className="kc-footer">
        {spec.priority && f[spec.priority] && <Priority field={f[spec.priority]} value={r[spec.priority]} />}
        {spec.date && r[spec.date] && <span className="kc-date">{formatDate(r[spec.date])}</span>}
        {spec.badge && f[spec.badge] && (f[spec.badge].type === "selection" ? <Badge field={f[spec.badge]} value={r[spec.badge]} /> : <span className="muted">{r[spec.badge]}</span>)}
        {spec.user && r[spec.user] && <span className="avatar avatar-sm" title={r[spec.user][1]} aria-label={r[spec.user][1]}>{initials(r[spec.user][1])}</span>}
      </div>
    </article>
  );
}
