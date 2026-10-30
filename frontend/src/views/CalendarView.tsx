import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { useState } from "react";
import { api, qs, type ModelMeta, type Rec } from "../lib/api";
import { formatValue } from "../lib/format";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export function CalendarView({ meta, domain, onOpen, onCreate }: { meta: ModelMeta; domain: any[]; onOpen: (id: number) => void; onCreate: (date: string) => void }) {
  const spec = meta.views.calendar;
  const [cursor, setCursor] = useState(() => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), 1); });
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(gridStart); d.setDate(gridStart.getDate() + i); return d; });
  const from = iso(days[0]);
  const to = iso(days[41]);
  const startField = spec.start as string;
  const stopField = spec.stop as string | null;
  const dated = meta.fields[startField]?.type === "date";
  const upper = dated ? to : `${to}T23:59:59`;
  const range = stopField ? [[startField, "<=", upper], [stopField, ">=", from]] : [[startField, ">=", from], [startField, "<=", upper]];
  const names = ["id", startField, stopField, spec.title].filter(Boolean).join(",");
  const query = useQuery<{ records: Rec[] }>({
    queryKey: ["records", meta.name, "calendar", domain, from],
    queryFn: () => api.get(`/api/models/${meta.name}/records${qs({ domain: [...domain, ...range], fields: names, limit: 500 })}`),
  });
  const titleField = meta.fields[spec.title];
  const today = iso(new Date());
  const dayKey = (v: string) => (v.length === 10 ? v : iso(new Date(v)));
  const eventsOn = (d: string) => (query.data?.records ?? []).filter((r) => {
    if (!r[startField]) return false;
    const start = dayKey(r[startField]);
    const stop = stopField && r[stopField] ? dayKey(r[stopField]) : start;
    return start <= d && d <= stop;
  });
  const timeLabel = (r: Rec) => { const v: string = r[startField]; return v.length > 10 ? `${new Date(v).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })} ` : ""; };
  return (
    <div className="calendar card">
      <div className="cal-toolbar">
        <button className="btn btn-sm" onClick={() => { const n = new Date(); setCursor(new Date(n.getFullYear(), n.getMonth(), 1)); }}>Today</button>
        <button className="icon-btn" aria-label="Previous month" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}><ChevronLeft size={17} /></button>
        <button className="icon-btn" aria-label="Next month" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}><ChevronRight size={17} /></button>
        <h2 aria-live="polite">{cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h2>
        {query.isFetching && <span className="muted">Loading...</span>}
      </div>
      {query.error ? <div className="empty"><p>{(query.error as Error).message}</p></div> : (
        <div className="cal-grid">
          {WEEKDAYS.map((w) => <div key={w} className="cal-head">{w}</div>)}
          {days.map((d) => {
            const key = iso(d);
            const events = eventsOn(key);
            return (
              <div key={key} className={`cal-cell ${d.getMonth() !== cursor.getMonth() ? "cal-out" : ""} ${key === today ? "cal-today" : ""}`}>
                <div className="cal-day"><span>{d.getDate()}</span>{meta.access.c && <button className="icon-btn cal-add" aria-label={`New on ${key}`} onClick={() => onCreate(key)}><Plus size={12} /></button>}</div>
                {events.slice(0, 4).map((r) => <button key={r.id} className="cal-event" onClick={() => onOpen(r.id)} title={formatValue(titleField, r[spec.title])}>{timeLabel(r)}{formatValue(titleField, r[spec.title]) || r.display_name}</button>)}
                {events.length > 4 && <div className="cal-more muted">+{events.length - 4} more</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
