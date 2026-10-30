import { useQuery } from "@tanstack/react-query";
import { Download, Star, Upload } from "lucide-react";
import { useState } from "react";
import Markdown from "react-markdown";
import { api, type FieldMeta } from "../lib/api";
import { formatValue, toneOf } from "../lib/format";
import { useToast } from "../lib/hooks";
import { Many2ManyInput, Many2OneInput } from "./Many2One";

export function Badge({ field, value }: { field?: FieldMeta; value: any }) {
  if (value === null || value === undefined || value === "") return null;
  return <span className={`badge tone-${toneOf(String(Array.isArray(value) ? "" : value))}`}>{formatValue(field, value)}</span>;
}

export function Priority({ field, value, onChange }: { field: FieldMeta; value: string; onChange?: (v: string) => void }) {
  const options = field.selection ?? [];
  const current = Number(value ?? 0);
  return (
    <span className="priority" role={onChange ? "radiogroup" : undefined} aria-label={field.label}>
      {options.slice(1).map((o, i) => {
        const on = current > i;
        return onChange ? (
          <button key={o[0]} type="button" role="radio" aria-checked={current === i + 1} aria-label={o[1]} className={`star ${on ? "on" : ""}`} onClick={(e) => { e.preventDefault(); e.stopPropagation(); onChange(current === i + 1 ? "0" : o[0]); }}><Star size={15} /></button>
        ) : <span key={o[0]} className={`star ${on ? "on" : ""}`}><Star size={13} aria-hidden="true" /></span>;
      })}
      {!onChange && <span className="sr-only">{options[current]?.[1]}</span>}
    </span>
  );
}

export function FieldDisplay({ field, value, widget }: { field: FieldMeta; value: any; widget?: string }) {
  if (widget === "badge") return <Badge field={field} value={value} />;
  if (widget === "priority") return <Priority field={field} value={value} />;
  if (widget === "markdown") return <div className="markdown"><Markdown>{value ?? ""}</Markdown></div>;
  if (field.type === "bool") return <input type="checkbox" checked={!!value} disabled aria-label={field.label} />;
  if (field.type === "many2many") return <span className="m2m-read">{(value ?? []).map((v: any) => <span key={v[0]} className="tag">{v[1]}</span>)}</span>;
  if (field.type === "file") return value ? <a className="file-link" href={`/api/attachments/${value[0]}`} download><Download size={14} /> {value[1]}</a> : null;
  if (field.type === "text") return <span className="pre-wrap">{value}</span>;
  if (field.name === "email" || field.name === "email_from" || field.name === "work_email") return value ? <a href={`mailto:${value}`}>{value}</a> : null;
  return <>{formatValue(field, value)}</>;
}

function toLocalInput(v: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function CheckboxList({ comodel, value, onChange }: { comodel: string; value: [number, string][]; onChange: (v: [number, string][]) => void }) {
  const options = useQuery<any>({ queryKey: ["options", comodel], queryFn: () => api.get(`/api/models/${comodel}/records?limit=200&fields=id`) });
  const selected = new Set(value.map((v) => v[0]));
  return (
    <div className="checkbox-grid">
      {options.data?.records.map((o: any) => (
        <label key={o.id} className="check-label">
          <input type="checkbox" checked={selected.has(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, [o.id, o.display_name]] : value.filter((v) => v[0] !== o.id))} /> {o.display_name}
        </label>
      ))}
    </div>
  );
}

function FileInput({ id, value, onChange }: { id?: string; value: any; onChange: (v: any) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function pick(files: FileList | null) {
    if (!files?.[0]) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", files[0]);
      const res = await api.upload("/api/attachments", form);
      onChange([res.id, res.name]);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="file-input">
      {value && <span className="tag">{value[1]}</span>}
      <label className="btn btn-sm"><Upload size={13} /> {busy ? "Uploading..." : value ? "Replace" : "Upload"}<input id={id} type="file" className="sr-only" onChange={(e) => pick(e.target.files)} /></label>
    </div>
  );
}

function MarkdownEditor({ id, value, onChange }: { id?: string; value: string; onChange: (v: string) => void }) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="md-editor">
      <div className="md-toolbar"><button type="button" className={`btn btn-sm ${!preview ? "btn-active" : ""}`} onClick={() => setPreview(false)}>Write</button><button type="button" className={`btn btn-sm ${preview ? "btn-active" : ""}`} onClick={() => setPreview(true)}>Preview</button></div>
      {preview ? <div className="markdown"><Markdown>{value}</Markdown></div> : <textarea id={id} className="input textarea-tall" value={value} onChange={(e) => onChange(e.target.value)} />}
    </div>
  );
}

export function FieldInput({ field, value, onChange, widget, id, invalid, compact }: { field: FieldMeta; value: any; onChange: (v: any) => void; widget?: string; id?: string; invalid?: boolean; compact?: boolean }) {
  const cls = `input ${invalid ? "input-invalid" : ""}`;
  if (widget === "priority") return <Priority field={field} value={value} onChange={onChange} />;
  if (widget === "markdown") return <MarkdownEditor id={id} value={value ?? ""} onChange={onChange} />;
  if (widget === "checkboxes") return <CheckboxList comodel={field.comodel!} value={value ?? []} onChange={onChange} />;
  switch (field.type) {
    case "many2one": return <Many2OneInput id={id} comodel={field.comodel!} domain={field.domain} value={value ?? null} onChange={onChange} invalid={invalid} />;
    case "many2many": return <Many2ManyInput id={id} comodel={field.comodel!} value={value ?? []} onChange={onChange} />;
    case "file": return <FileInput id={id} value={value} onChange={onChange} />;
    case "bool": return <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
    case "text": return <textarea id={id} className={cls} rows={compact ? 1 : 3} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
    case "int": case "float": case "money":
      return <input id={id} className={`${cls} num`} type="number" step={field.type === "int" ? "1" : "any"} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
    case "date": return <input id={id} className={cls} type="date" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} />;
    case "datetime": return <input id={id} className={cls} type="datetime-local" value={toLocalInput(value)} onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString() : null)} />;
    case "selection":
      if (widget === "radio") {
        return <div className="radio-row">{field.selection?.map((o) => <label key={o[0]} className="check-label"><input type="radio" name={field.name} checked={value === o[0]} onChange={() => onChange(o[0])} /> {o[1]}</label>)}</div>;
      }
      return <select id={id} className={cls} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}><option value="" />{field.selection?.map((o) => <option key={o[0]} value={o[0]}>{o[1]}</option>)}</select>;
    default: return <input id={id} className={cls} type={field.name.includes("email") ? "email" : "text"} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
  }
}
