import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Printer, Settings2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, qs, type ActionMeta, type FieldMeta, type ModelMeta, type Rec } from "../lib/api";
import { FieldDisplay, FieldInput } from "../fields/FieldInput";
import { linesFromRecords, One2ManyTable, type LineRow } from "../fields/One2ManyTable";
import { matches, substitute } from "../lib/domain";
import { formatValue } from "../lib/format";
import { useBootstrap, useToast } from "../lib/hooks";
import { Dropdown } from "../shell/Navbar";
import { Icon } from "../shell/icons";
import { ActionDialog } from "./ActionDialog";
import { Chatter } from "./Chatter";
import type { Crumb } from "./ActionPage";

type Item = string | { field: string; widget?: string; invisible?: any[]; readonly?: any[]; totals?: string[] };
const itemName = (i: Item) => (typeof i === "string" ? i : i.field);

/** What the API needs for a value: relations as ids. */
function toWire(f: FieldMeta, v: any) {
  if (f.type === "many2one" || f.type === "file") return Array.isArray(v) ? v[0] : v ?? null;
  if (f.type === "many2many") return (v ?? []).map((x: any) => x[0]);
  return v;
}

/** Line values come back from pickers as [id, name] pairs. */
function lineToWire(vals: Rec): Rec {
  return Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, Array.isArray(v) && v.length === 2 && typeof v[0] === "number" ? v[0] : v]));
}

type Props = { meta: ModelMeta; id: number | null; base: string; title: string; appKey: string; context: Rec; search: string };

export function FormView({ meta, id, base, title, appKey, context, search }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const toast = useToast();
  const { data: boot } = useBootstrap();
  const form = meta.views.form ?? { sections: [], buttons: [], smart: [] };
  const urlContext = useMemo(() => {
    try {
      return JSON.parse(new URLSearchParams(search).get("context") ?? "{}");
    } catch {
      return {};
    }
  }, [search]);

  const record = useQuery<Rec>({ queryKey: ["record", meta.name, id], enabled: id !== null, queryFn: () => api.get(`/api/models/${meta.name}/records/${id}`) });
  const defaults = useQuery<Rec>({ queryKey: ["defaults", meta.name], enabled: id === null && meta.access.c, gcTime: 0, queryFn: () => api.get(`/api/models/${meta.name}/defaults`) });
  const [changes, setChanges] = useState<Rec>({});
  const [lines, setLines] = useState<Record<string, { rows: LineRow[]; removed: number[] }>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [invalid, setInvalid] = useState<string[]>([]);
  const [pending, setPending] = useState<ActionMeta | null>(null);

  const initial = useMemo(() => {
    if (id !== null) return record.data ?? {};
    const out: Rec = { ...(defaults.data ?? {}) };
    for (const [k, v] of Object.entries({ ...context, ...urlContext })) if (k.startsWith("default_")) out[k.slice(8)] = v;
    return out;
  }, [id, record.data, defaults.data, context, urlContext]);
  const values = useMemo(() => {
    const v = { ...initial, ...changes };
    for (const [k, l] of Object.entries(lines)) v[k] = l.rows.map((r) => r.values);
    return v;
  }, [initial, changes, lines]);

  // A fresh read replaces the local copies of the inline lines.
  useEffect(() => setLines({}), [record.dataUpdatedAt]);

  const locked = id !== null && !!meta.locked_when && matches(meta.locked_when, initial);
  const editable = id === null ? meta.access.c : meta.access.w && !locked;
  const dirty = Object.keys(changes).length > 0 || Object.values(lines).some((l) => l.removed.length > 0 || l.rows.some((r) => r.id === undefined || Object.keys(r.changed).length > 0));
  const crumbs: Crumb[] = (location.state as any)?.crumbs ?? [{ label: title, path: base }];
  const backPath = crumbs[crumbs.length - 1].path;
  const heading = form.title && meta.fields[form.title] ? formatValue(meta.fields[form.title], values[form.title]) : values.display_name;

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function setField(name: string, value: any) {
    const merged = { ...values, [name]: value };
    setChanges((old) => ({ ...old, [name]: value }));
    setInvalid((old) => old.filter((n) => n !== name));
    if (meta.fields[name]?.onchange) {
      api.post(`/api/models/${meta.name}/onchange`, { field: name, values: merged }).then((res) => {
        if (res && Object.keys(res).length) setChanges((old) => ({ ...old, ...res }));
      }).catch((e) => toast(e.message, "error"));
    }
  }

  function buildPayload(): Rec | null {
    const allItems: Item[] = form.sections.flatMap((s: any) => [...(s.columns ?? []).flat(), ...(s.tabs ?? []).flatMap((t: any) => t.fields)]);
    const missing = [...allItems.map(itemName), form.title].filter((n) => {
      const f = n && meta.fields[n];
      if (!f || !f.required || f.readonly) return false;
      const v = values[n];
      return v === null || v === undefined || v === "";
    }) as string[];
    if (missing.length) {
      setInvalid(missing);
      setError(`Fill in: ${missing.map((n) => meta.fields[n].label).join(", ")}.`);
      return null;
    }
    const payload: Rec = {};
    // Server-side defaults for read-only fields stay on the server.
    for (const [k, v] of Object.entries(id === null ? { ...initial, ...changes } : changes)) {
      const f = meta.fields[k];
      if (f && f.type !== "one2many" && !f.readonly) payload[k] = toWire(f, v);
    }
    for (const [k, l] of Object.entries(lines)) {
      const commands: any[] = l.removed.map((rid) => [2, rid, 0]);
      for (const r of l.rows) {
        if (r.id === undefined) commands.push([0, 0, lineToWire(r.changed)]);
        else if (Object.keys(r.changed).length) commands.push([1, r.id, lineToWire(r.changed)]);
      }
      if (commands.length) payload[k] = commands;
    }
    return payload;
  }

  async function save(): Promise<number | null> {
    setError("");
    const payload = buildPayload();
    if (!payload) return null;
    setSaving(true);
    try {
      if (id === null) {
        const created = await api.post(`/api/models/${meta.name}/records`, payload);
        client.invalidateQueries({ queryKey: ["records", meta.name] });
        navigate(`${base}/${created.id}`, { replace: true, state: location.state });
        return created.id;
      }
      await api.patch(`/api/models/${meta.name}/records/${id}`, payload);
      setChanges({});
      setLines({});
      setInvalid([]);
      await Promise.all([record.refetch(), client.invalidateQueries({ queryKey: ["records", meta.name] }), client.invalidateQueries({ queryKey: ["chatter", meta.name, id] })]);
      return id;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setSaving(false);
    }
  }

  function discard() {
    setChanges({});
    setLines({});
    setError("");
    setInvalid([]);
    if (id === null) navigate(backPath);
  }

  async function runAction(action: ActionMeta, params: Rec = {}) {
    const res = await api.post(`/api/models/${meta.name}/actions/${action.key}`, { ids: [id], params });
    setPending(null);
    await Promise.all(["record", "records", "chatter", "smart", "bootstrap"].map((k) => client.invalidateQueries({ queryKey: [k] })));
    if (res?.type === "open") navigate(`/a/${appKey}/m/${res.model}/${res.id}`, { state: { crumbs: [...crumbs, { label: heading || meta.label, path: location.pathname + location.search }] } });
    if (res?.type === "notify") toast(res.message);
  }

  async function clickAction(key: string) {
    const action = meta.actions[key];
    if (!action) return;
    if (dirty && !(await save())) return;
    if (action.params || action.confirm) {
      setPending(action);
      return;
    }
    try {
      setError("");
      await runAction(action);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete this ${meta.label.toLowerCase()}?`)) return;
    try {
      await api.del(`/api/models/${meta.name}/records/${id}`);
      client.invalidateQueries({ queryKey: ["records", meta.name] });
      toast("Deleted.");
      navigate(backPath);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (id !== null && record.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (id !== null && record.error) return <div className="empty"><h2>This record could not be opened</h2><p>{(record.error as Error).message}</p><Link className="btn" to={backPath}>Back</Link></div>;

  const renderField = (item: Item, inTab: boolean) => {
    const name = itemName(item);
    const f = meta.fields[name];
    if (!f) return null;
    const opts: Exclude<Item, string> = typeof item === "string" ? { field: item } : item;
    if (opts.invisible && matches(opts.invisible, values)) return null;
    const readonly = !editable || f.readonly || (opts.readonly ? matches(opts.readonly, values) : false);
    if (f.type === "one2many") {
      const state = lines[name] ?? { rows: linesFromRecords(initial[name]), removed: [] };
      return (
        <div key={name} className="o2m-block">
          <One2ManyTable field={f} rows={state.rows} removed={state.removed} parent={values} readonly={readonly} onChange={(rows, removed) => setLines((old) => ({ ...old, [name]: { rows, removed } }))} />
          {opts.totals && id !== null && (
            <dl className="totals">{opts.totals.filter((t) => meta.fields[t]).map((t, i, list) => <div key={t} className={i === list.length - 1 ? "totals-final" : ""}><dt>{meta.fields[t].label}</dt><dd>{formatValue(meta.fields[t], initial[t])}</dd></div>)}</dl>
          )}
        </div>
      );
    }
    const inputId = `f-${name}`;
    if (inTab && (f.type === "text" || opts.widget === "checkboxes" || opts.widget === "markdown")) {
      return <div key={name} className="tab-wide">{readonly ? <FieldDisplay field={f} value={values[name]} widget={opts.widget} /> : <FieldInput id={inputId} field={f} value={values[name]} widget={opts.widget} onChange={(v) => setField(name, v)} />}</div>;
    }
    return (
      <div key={name} className="form-row">
        <label htmlFor={inputId} title={f.help}>{f.label}{f.required && !readonly && <span className="req" aria-hidden="true"> *</span>}</label>
        <div className="form-value">
          {readonly ? <FieldDisplay field={f} value={values[name]} widget={opts.widget} /> : <FieldInput id={inputId} field={f} value={values[name]} widget={opts.widget} invalid={invalid.includes(name)} onChange={(v) => setField(name, v)} />}
        </div>
      </div>
    );
  };

  const titleField = form.title ? meta.fields[form.title] : null;
  const childCrumbs = [...crumbs, { label: heading || meta.label, path: location.pathname + location.search }];
  return (
    <div className={`form-page ${form.chatter && id !== null ? "with-chatter" : ""}`}>
      <div className="form-main">
        <div className="control-panel form-cp">
          <nav className="breadcrumb" aria-label="Breadcrumb">
            {crumbs.map((c) => <span key={c.path} className="crumb"><Link to={c.path}>{c.label}</Link><ChevronRight size={13} aria-hidden="true" /></span>)}
            <span aria-current="page">{id === null ? "New" : heading || meta.label}</span>
          </nav>
          {dirty && editable && (
            <div className="save-group">
              <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}><Check size={14} /> {saving ? "Saving..." : "Save"}</button>
              <button className="btn btn-sm" onClick={discard}>Discard</button>
            </div>
          )}
          {id === null && !dirty && <button className="btn btn-sm" onClick={discard}>Discard</button>}
          <div className="cp-spacer" />
          {id !== null && (form.printable || (meta.access.d && !locked)) && (
            <Dropdown ariaLabel="Record actions" className="icon-btn" align="right" label={<Settings2 size={16} />}>
              {form.printable && <a className="dropdown-item" href={`/print/${form.printable}/${meta.name}/${id}`} target="_blank" rel="noreferrer"><Printer size={14} /> Print</a>}
              {meta.access.d && !locked && <button className="dropdown-item" onClick={remove}><Trash2 size={14} /> Delete</button>}
            </Dropdown>
          )}
        </div>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        {(form.buttons.length > 0 || form.status) && (
          <div className="form-header">
            <div className="form-buttons">
              {id !== null && form.buttons.filter((b: any) => matches(b.when, values)).map((b: any) => <button key={b.action} className={`btn btn-sm ${b.primary ? "btn-primary" : ""}`} disabled={saving} onClick={() => clickAction(b.action)}>{b.label}</button>)}
            </div>
            {form.status && meta.fields[form.status] && <StatusBar model={meta} field={meta.fields[form.status]} value={values[form.status]} clickable={!!form.status_clickable && editable} onSet={(v) => setField(form.status, v)} />}
          </div>
        )}
        <div className="sheet card">
          {id !== null && form.smart?.length > 0 && <div className="smart-buttons">{form.smart.map((s: any) => <SmartButton key={s.label} s={s} record={initial} appKey={appKey} crumbs={childCrumbs} uid={boot?.user.id} />)}</div>}
          {titleField && (
            <div className="sheet-title">
              {editable && !titleField.readonly ? (
                <input className={`title-input ${invalid.includes(form.title) ? "input-invalid" : ""}`} aria-label={titleField.label} placeholder={titleField.label} value={values[form.title] ?? ""} onChange={(e) => setField(form.title, e.target.value)} />
              ) : <h1>{heading || (id === null ? "New" : "")}</h1>}
            </div>
          )}
          {form.sections.map((s: any, i: number) => (
            <section key={i}>
              {s.title && <h2 className="section-title">{s.title}</h2>}
              {s.columns?.length > 0 && <div className="form-columns">{s.columns.map((column: Item[], ci: number) => <div key={ci} className="form-col">{column.map((it) => renderField(it, false))}</div>)}</div>}
              {s.tabs?.length > 0 && <Tabs tabs={s.tabs.filter((t: any) => t.fields.some((it: Item) => !(typeof it !== "string" && it.invisible && matches(it.invisible, values))))} render={renderField} />}
            </section>
          ))}
        </div>
      </div>
      {form.chatter && id !== null && <Chatter model={meta.name} id={id} />}
      {pending && <ActionDialog action={pending} record={initial} onClose={() => setPending(null)} onSubmit={(p) => runAction(pending, p)} />}
    </div>
  );
}

function Tabs({ tabs, render }: { tabs: { label: string; fields: Item[] }[]; render: (i: Item, inTab: boolean) => ReactNode }) {
  const [active, setActive] = useState(0);
  const current = tabs[Math.min(active, tabs.length - 1)];
  if (!current) return null;
  return (
    <div className="tabs">
      <div className="tab-list" role="tablist">
        {tabs.map((t, i) => <button key={t.label} role="tab" aria-selected={t === current} className={t === current ? "active" : ""} onClick={() => setActive(i)}>{t.label}</button>)}
      </div>
      <div className="tab-panel" role="tabpanel" aria-label={current.label}>{current.fields.map((f) => render(f, true))}</div>
    </div>
  );
}

function StatusBar({ model, field, value, clickable, onSet }: { model: ModelMeta; field: FieldMeta; value: any; clickable: boolean; onSet: (v: any) => void }) {
  const stages = useQuery<[any, string][]>({
    queryKey: ["group-values", model.name, field.name],
    queryFn: () => api.get(`/api/models/${model.name}/group_values${qs({ field: field.name })}`),
  });
  const current = Array.isArray(value) ? value[0] : value;
  return (
    <ol className="statusbar" aria-label={field.label}>
      {stages.data?.map(([k, label]) => (
        <li key={String(k)} className={k === current ? "current" : ""} aria-current={k === current ? "step" : undefined}>
          {clickable ? <button onClick={() => onSet(field.type === "many2one" ? [k, label] : k)}>{label}</button> : <span>{label}</span>}
        </li>
      ))}
    </ol>
  );
}

function SmartButton({ s, record, appKey, crumbs, uid }: { s: any; record: Rec; appKey: string; crumbs: Crumb[]; uid?: number }) {
  const domain = substitute(s.domain, { ...record, uid });
  const count = useQuery<{ total: number }>({ queryKey: ["smart", s.model, domain], queryFn: () => api.get(`/api/models/${s.model}/records${qs({ domain, limit: 1, fields: "id" })}`) });
  return (
    <Link className="smart-btn" to={`/a/${appKey}/m/${s.model}${qs({ domain, title: s.label })}`} state={{ crumbs }}>
      <Icon name={s.icon} size={18} /><span><b>{count.data?.total ?? "-"}</b> {s.label}</span>
    </Link>
  );
}
