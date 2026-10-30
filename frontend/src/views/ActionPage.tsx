import { useQueryClient } from "@tanstack/react-query";
import { BarChart3, CalendarDays, ChevronLeft, ChevronRight, Filter, Kanban, Layers, List, Plus, Search, Settings2, Table2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type AppMeta, type MenuItem, type ModelMeta, type Rec } from "../lib/api";
import { substitute } from "../lib/domain";
import { useBootstrap, useModelMeta, useToast } from "../lib/hooks";
import { Dropdown } from "../shell/Navbar";
import { Attendance } from "../clients/Attendance";
import { Dashboard } from "../clients/Dashboard";
import { Discuss } from "../clients/Discuss";
import { PosTill } from "../clients/Pos";
import { Report } from "../clients/Report";
import { CalendarView } from "./CalendarView";
import { FormView } from "./FormView";
import { GraphView } from "./GraphView";
import { KanbanView } from "./KanbanView";
import { ListView } from "./ListView";
import { PivotView } from "./PivotView";

export type Crumb = { label: string; path: string };
export type SearchState = { terms: string[]; filters: string[]; groupBy: string | null };
const EMPTY_SEARCH: SearchState = { terms: [], filters: [], groupBy: null };
// Survives leaving a list to open a record and coming back.
const searchMemory = new Map<string, SearchState>();

const VIEW_ICONS: Record<string, { icon: typeof List; label: string }> = {
  list: { icon: List, label: "List" }, kanban: { icon: Kanban, label: "Kanban" }, calendar: { icon: CalendarDays, label: "Calendar" }, graph: { icon: BarChart3, label: "Graph" }, pivot: { icon: Table2, label: "Pivot" },
};

function flatten(menus: MenuItem[]): MenuItem[] {
  return menus.flatMap((m) => (m.children ? flatten(m.children) : [m]));
}

function parseJson<T>(raw: string | null, fallback: T): T {
  try {
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function ErrorState({ message }: { message: string }) {
  return <div className="empty"><h2>This page could not be shown</h2><p>{message}</p><Link className="btn" to="/">Back to apps</Link></div>;
}

export function ActionPage() {
  const params = useParams();
  const location = useLocation();
  const { data: boot } = useBootstrap();
  const app = boot?.apps.find((a) => a.key === params.app);
  if (!boot) return null;
  if (!app && !params.model) return <ErrorState message="You do not have access to this app." />;
  if (!params.action && !params.model) {
    const first = flatten(app!.menus)[0];
    return first ? <Navigate to={`/a/${app!.key}/${first.key}`} replace /> : <ErrorState message="This app has no pages." />;
  }
  if (params.model) {
    const query = new URLSearchParams(location.search);
    const item: MenuItem = {
      key: params.model, label: query.get("title") ?? "", model: params.model, views: ["list", "kanban", "form", "graph", "pivot", "calendar"],
      domain: parseJson<any[]>(query.get("domain"), []), context: parseJson<Rec>(query.get("context"), {}), client: null, children: null,
    };
    return <ModelAction key={`${params.model}:${query.get("domain") ?? ""}`} app={app} item={item} id={params.id} generic />;
  }
  const item = flatten(app!.menus).find((m) => m.key === params.action);
  if (!item) return <ErrorState message="That page does not exist, or you do not have access to it." />;
  if (item.client) return <ClientAction key={item.key} client={item.client} />;
  return <ModelAction key={`${app!.key}/${item.key}`} app={app} item={item} id={params.id} />;
}

function ClientAction({ client }: { client: string }) {
  if (client === "dashboard" || client === "accounting_overview") return <Dashboard />;
  if (client === "discuss") return <Discuss />;
  if (client === "pos") return <PosTill />;
  if (client === "attendance") return <Attendance />;
  if (client.startsWith("report:")) return <Report key={client} reportKey={client.slice(7)} />;
  return <ErrorState message="Unknown page." />;
}

function ModelAction({ app, item, id, generic }: { app?: AppMeta; item: MenuItem; id?: string; generic?: boolean }) {
  const meta = useModelMeta(item.model);
  const location = useLocation();
  if (meta.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (meta.error || !meta.data) return <ErrorState message={(meta.error as Error)?.message ?? "This list could not be loaded."} />;
  const title = item.label || meta.data.label;
  const appKey = app?.key ?? location.pathname.split("/")[2];
  const base = generic ? `/a/${appKey}/m/${item.model}` : `/a/${appKey}/${item.key}`;
  if (id) return <FormView key={id} meta={meta.data} id={id === "new" ? null : Number(id)} base={base} title={title} appKey={appKey} context={item.context} search={location.search} />;
  return <Collection item={item} meta={meta.data} base={base} title={title} />;
}

function Collection({ item, meta, base, title }: { item: MenuItem; meta: ModelMeta; base: string; title: string }) {
  const { data: boot } = useBootstrap();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const views = item.views.filter((v) => v !== "form" && meta.views[v]);
  const requested = params.get("view");
  const view = requested && views.includes(requested) ? requested : views[0] ?? "list";
  const memoryKey = `${base}${location.search.replace(/[?&]view=\w+/, "")}`;
  const [search, setSearchState] = useState<SearchState>(() => searchMemory.get(memoryKey) ?? EMPTY_SEARCH);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const setSearch = (s: SearchState) => { searchMemory.set(memoryKey, s); setSearchState(s); setOffset(0); };

  const domain = useMemo(() => {
    const ctx = { uid: boot?.user.id };
    const out: any[] = [...substitute(item.domain, ctx)];
    const spec = meta.views.search ?? { fields: [], filters: [] };
    const active = (spec.filters ?? []).filter((f: any) => search.filters.includes(f.key));
    if (active.length) {
      // Filters in the same menu widen each other, as in Odoo.
      const exprs = active.map((f: any) => substitute(f.domain, ctx));
      out.push(...Array(active.length - 1).fill("|"));
      for (const e of exprs) {
        if (e.length > 1) out.push(...Array(e.length - 1).fill("&"));
        out.push(...e);
      }
    }
    const fields: string[] = spec.fields?.length ? spec.fields : [meta.rec_name];
    for (const term of search.terms) {
      out.push(...Array(fields.length - 1).fill("|"));
      for (const f of fields) out.push([f, "ilike", term]);
    }
    return out;
  }, [item.domain, search, meta, boot?.user.id]);

  const listPath = location.pathname + location.search;
  const crumbState = { crumbs: [{ label: title, path: listPath }] };
  const newContext = Object.keys(item.context ?? {}).length ? `?context=${encodeURIComponent(JSON.stringify(item.context))}` : "";
  const open = (id: number | string) => navigate(`${base}/${id}`, { state: crumbState });
  const pageSize = 80;

  const exportFields = (meta.views.list?.columns ?? []).map((c: any) => (typeof c === "string" ? c : c.field)).join(",");
  const exportUrl = `/api/models/${meta.name}/export.csv?${new URLSearchParams({ domain: JSON.stringify(domain), fields: exportFields }).toString()}`;

  async function importCsv(files: FileList | null) {
    if (!files?.[0]) return;
    const form = new FormData();
    form.append("file", files[0]);
    try {
      const res = await api.upload(`/api/models/${meta.name}/import`, form);
      toast(`Imported ${res.created} record(s).`);
      client.invalidateQueries({ queryKey: ["records", meta.name] });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      if (importRef.current) importRef.current.value = "";
    }
  }

  function createOn(date: string) {
    const context = { ...item.context, [`default_${meta.views.calendar.start}`]: date };
    navigate(`${base}/new?context=${encodeURIComponent(JSON.stringify(context))}`, { state: crumbState });
  }

  return (
    <div className="action-page">
      <div className="control-panel">
        <div className="cp-left">
          {meta.access.c && <Link className="btn btn-primary" to={`${base}/new${newContext}`} state={crumbState}><Plus size={15} /> New</Link>}
          <h1 className="cp-title">{title}</h1>
          <Dropdown ariaLabel="List actions" className="icon-btn" label={<Settings2 size={16} />}>
            {exportFields && <a className="dropdown-item" href={exportUrl}>Export to CSV</a>}
            {meta.access.c && <button className="dropdown-item" onClick={() => importRef.current?.click()}>Import from CSV</button>}
          </Dropdown>
          <input ref={importRef} type="file" accept=".csv,text/csv" className="sr-only" tabIndex={-1} aria-label="Import CSV file" onChange={(e) => importCsv(e.target.files)} />
        </div>
        <SearchBar meta={meta} search={search} setSearch={setSearch} />
        <div className="cp-right">
          {view === "list" && total !== null && total > 0 && (
            <div className="pager">
              <span>{offset + 1}-{Math.min(offset + pageSize, total)} / {total}</span>
              <button className="icon-btn" aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}><ChevronLeft size={16} /></button>
              <button className="icon-btn" aria-label="Next page" disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}><ChevronRight size={16} /></button>
            </div>
          )}
          {views.length > 1 && (
            <div className="view-switch" role="group" aria-label="Switch view">
              {views.map((v) => {
                const { icon: I, label } = VIEW_ICONS[v];
                return <button key={v} className={`icon-btn ${v === view ? "active" : ""}`} aria-label={label} aria-pressed={v === view} title={label} onClick={() => { const next = new URLSearchParams(params); next.set("view", v); setParams(next, { replace: true }); }}><I size={16} /></button>;
              })}
            </div>
          )}
        </div>
      </div>
      <div className="view-area">
        {view === "list" && <ListView meta={meta} domain={domain} groupBy={search.groupBy} offset={offset} limit={pageSize} onOpen={open} onTotal={setTotal} />}
        {view === "kanban" && <KanbanView meta={meta} domain={domain} groupBy={search.groupBy} onOpen={open} />}
        {view === "calendar" && <CalendarView meta={meta} domain={domain} onOpen={open} onCreate={createOn} />}
        {view === "graph" && <GraphView meta={meta} domain={domain} groupBy={search.groupBy} />}
        {view === "pivot" && <PivotView meta={meta} domain={domain} groupBy={search.groupBy} />}
      </div>
    </div>
  );
}

function SearchBar({ meta, search, setSearch }: { meta: ModelMeta; search: SearchState; setSearch: (s: SearchState) => void }) {
  const [text, setText] = useState("");
  const spec = meta.views.search ?? { filters: [], group_by: [] };
  const filters: Rec[] = spec.filters ?? [];
  const groupBys: string[] = (spec.group_by ?? []).filter((g: string) => meta.fields[g.split(":")[0]]);
  useEffect(() => setText(""), [meta.name]);
  const groupLabel = (g: string) => {
    const [name, gran] = g.split(":");
    return `${meta.fields[name]?.label ?? name}${gran ? ` (${gran})` : ""}`;
  };
  function submit() {
    const t = text.trim();
    if (t) setSearch({ ...search, terms: [...search.terms, t] });
    setText("");
  }
  return (
    <div className="search-bar">
      <div className="search-box">
        <Search size={14} aria-hidden="true" />
        {search.terms.map((t) => <span key={t} className="facet">{t}<button aria-label={`Remove search ${t}`} onClick={() => setSearch({ ...search, terms: search.terms.filter((x) => x !== t) })}><X size={11} /></button></span>)}
        {filters.filter((f) => search.filters.includes(f.key)).map((f) => <span key={f.key} className="facet facet-filter"><Filter size={10} /> {f.label}<button aria-label={`Remove filter ${f.label}`} onClick={() => setSearch({ ...search, filters: search.filters.filter((x) => x !== f.key) })}><X size={11} /></button></span>)}
        {search.groupBy && <span className="facet facet-group"><Layers size={10} /> {groupLabel(search.groupBy)}<button aria-label="Remove grouping" onClick={() => setSearch({ ...search, groupBy: null })}><X size={11} /></button></span>}
        <input aria-label="Search" placeholder="Search..." value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Backspace" && !text && search.terms.length) setSearch({ ...search, terms: search.terms.slice(0, -1) }); }} />
      </div>
      <div className="search-menus">
        {filters.length > 0 && (
          <Dropdown className="btn btn-ghost" label={<><Filter size={13} /> Filters</>}>
            {filters.map((f) => <button key={f.key} className={`dropdown-item ${search.filters.includes(f.key) ? "checked" : ""}`} aria-pressed={search.filters.includes(f.key)} onClick={() => setSearch({ ...search, filters: search.filters.includes(f.key) ? search.filters.filter((x) => x !== f.key) : [...search.filters, f.key] })}>{f.label}</button>)}
          </Dropdown>
        )}
        {groupBys.length > 0 && (
          <Dropdown className="btn btn-ghost" label={<><Layers size={13} /> Group By</>}>
            {groupBys.map((g) => <button key={g} className={`dropdown-item ${search.groupBy === g ? "checked" : ""}`} aria-pressed={search.groupBy === g} onClick={() => setSearch({ ...search, groupBy: search.groupBy === g ? null : g })}>{groupLabel(g)}</button>)}
          </Dropdown>
        )}
      </div>
    </div>
  );
}
