import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { ActionMeta, Rec } from "../lib/api";
import { localToday } from "../lib/domain";
import { Many2OneInput } from "../fields/Many2One";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not(.icon-btn)")?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); previous?.focus(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="modal-header"><h2>{title}</h2><button className="icon-btn" aria-label="Close" onClick={onClose}><X size={18} /></button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/** Collects an action's parameters and/or its confirmation before running it. */
export function ActionDialog({ action, record, onSubmit, onClose }: { action: ActionMeta; record?: Rec; onSubmit: (params: Rec) => Promise<void>; onClose: () => void }) {
  const [values, setValues] = useState<Rec>(() => {
    const init: Rec = {};
    for (const p of action.params ?? []) {
      if (p.default_field && record) init[p.name] = record[p.default_field];
      if (p.default_today) init[p.name] = localToday();
    }
    return init;
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: any) => setValues((old) => ({ ...old, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit(values);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal title={action.label} onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        {action.confirm && <p>{action.confirm}</p>}
        {action.params?.map((p) => (
          <div key={p.name} className="stack-field">
            <label htmlFor={`param-${p.name}`}>{p.label}</label>
            {p.type === "many2one" ? (
              <Many2OneInput id={`param-${p.name}`} comodel={p.comodel!} domain={p.domain} value={values[p.name] ?? null} onChange={(v) => set(p.name, v)} />
            ) : (
              <input id={`param-${p.name}`} className="input" required={p.required}
                type={p.type === "password" ? "password" : p.type === "date" ? "date" : p.type === "money" ? "number" : "text"}
                step={p.type === "money" ? "0.01" : undefined} autoComplete={p.type === "password" ? "new-password" : undefined}
                value={values[p.name] ?? ""} onChange={(e) => set(p.name, e.target.value)} />
            )}
          </div>
        ))}
        <div className="modal-footer">
          <button className="btn btn-primary" disabled={busy}>{busy ? "Working..." : action.label}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
