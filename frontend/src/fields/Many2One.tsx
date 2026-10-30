import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { api, qs } from "../lib/api";

type Pair = [number, string];

/** Autocomplete over a related model. The value is [id, name] or null. */
export function Many2OneInput({ comodel, value, onChange, domain, id, invalid }: { comodel: string; value: Pair | null; onChange: (v: Pair | null) => void; domain?: any[] | null; id?: string; invalid?: boolean }) {
  const [text, setText] = useState(value?.[1] ?? "");
  const [options, setOptions] = useState<Pair[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const listId = useId();
  useEffect(() => setText(value?.[1] ?? ""), [value]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const q = text === (value?.[1] ?? "") ? "" : text;
    const timer = window.setTimeout(async () => {
      try {
        const res = await api.get<Pair[]>(`/api/models/${comodel}/name_search${qs({ q, domain: domain?.length ? domain : undefined, limit: 12 })}`);
        if (!cancelled) { setOptions(res); setActive(0); setFailed(false); }
      } catch {
        if (!cancelled) { setOptions([]); setFailed(true); }
      }
    }, 150);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [text, open, comodel, JSON.stringify(domain)]); // eslint-disable-line

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) { setOpen(false); setText(value?.[1] ?? ""); }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [value]);

  function pick(o: Pair) { onChange(o); setOpen(false); }

  return (
    <div className="m2o" ref={box}>
      <input id={id} className={`input ${invalid ? "input-invalid" : ""}`} role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list" autoComplete="off" value={text}
        onFocus={() => setOpen(true)} onChange={(e) => { setText(e.target.value); setOpen(true); if (!e.target.value && value) onChange(null); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, options.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && open && options[active]) { e.preventDefault(); pick(options[active]); }
          else if (e.key === "Escape") { setOpen(false); setText(value?.[1] ?? ""); }
          else if (e.key === "Tab") { setOpen(false); setText(value?.[1] ?? ""); }
        }} />
      {value && <button type="button" className="m2o-clear icon-btn" aria-label="Clear" tabIndex={-1} onClick={() => { onChange(null); setText(""); }}><X size={13} /></button>}
      {open && (
        <ul className="m2o-list" id={listId} role="listbox">
          {options.map((o, i) => (
            <li key={o[0]} role="option" aria-selected={i === active} className={i === active ? "active" : ""} onMouseDown={(e) => { e.preventDefault(); pick(o); }}>{o[1] || `#${o[0]}`}</li>
          ))}
          {options.length === 0 && <li className="muted m2o-empty">{failed ? "You cannot browse this list." : "No results"}</li>}
        </ul>
      )}
    </div>
  );
}

export function Many2ManyInput({ comodel, value, onChange, id }: { comodel: string; value: Pair[]; onChange: (v: Pair[]) => void; id?: string }) {
  const [pending, setPending] = useState<Pair | null>(null);
  return (
    <div className="m2m">
      {value.map((v) => (
        <span key={v[0]} className="tag">{v[1]}<button type="button" className="tag-x" aria-label={`Remove ${v[1]}`} onClick={() => onChange(value.filter((x) => x[0] !== v[0]))}><X size={11} /></button></span>
      ))}
      <Many2OneInput id={id} comodel={comodel} value={pending} domain={value.length ? [["id", "not in", value.map((v) => v[0])]] : undefined}
        onChange={(v) => { if (v) { onChange([...value, v]); setPending(null); } }} />
    </div>
  );
}
