import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Download, MessageSquare, Paperclip } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../lib/api";
import { localToday } from "../lib/domain";
import { formatDate, formatDateTime, initials } from "../lib/format";
import { useToast } from "../lib/hooks";
import { Modal } from "./ActionDialog";

export function Chatter({ model, id }: { model: string; id: number }) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const query = useQuery<any>({ queryKey: ["chatter", model, id], queryFn: () => api.get(`/api/mail/${model}/${id}`) });

  async function send() {
    if (!body.trim()) return;
    setBusy(true);
    try {
      await api.post(`/api/mail/${model}/${id}`, { body });
      setBody("");
      await query.refetch();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.[0]) return;
    const form = new FormData();
    form.append("file", files[0]);
    form.append("res_model", model);
    form.append("res_id", String(id));
    try {
      await api.upload("/api/attachments", form);
      await query.refetch();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function markDone(activity: number) {
    try {
      await api.post(`/api/activities/${activity}/done`);
      await query.refetch();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  return (
    <aside className="chatter" aria-label="Conversation">
      <div className="composer">
        <label className="sr-only" htmlFor="chatter-body">Add a note</label>
        <textarea id="chatter-body" className="input" rows={2} placeholder="Log a note..." value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="composer-actions">
          <button className="btn btn-sm btn-primary" disabled={busy || !body.trim()} onClick={send}><MessageSquare size={13} /> Log note</button>
          <button className="btn btn-sm" onClick={() => setScheduling(true)}><CalendarClock size={13} /> Activity</button>
          <button className="btn btn-sm" onClick={() => fileRef.current?.click()}><Paperclip size={13} /> Attach</button>
          <input ref={fileRef} type="file" className="sr-only" tabIndex={-1} aria-label="Attach a file" onChange={(e) => upload(e.target.files)} />
        </div>
      </div>
      {query.error && <div className="banner banner-error">{(query.error as Error).message}</div>}
      {query.data?.activities.length > 0 && (
        <section className="chatter-section">
          <h3>Planned activities</h3>
          {query.data.activities.map((a: any) => (
            <div key={a.id} className="chatter-activity">
              <div><strong>{a.name}</strong><div className="muted">{a.user} · due {formatDate(a.date_deadline)}</div></div>
              <button className="btn btn-sm" onClick={() => markDone(a.id)}>Done</button>
            </div>
          ))}
        </section>
      )}
      {query.data?.attachments.length > 0 && (
        <section className="chatter-section">
          <h3>Files</h3>
          {query.data.attachments.map((a: any) => <a key={a.id} className="file-link" href={`/api/attachments/${a.id}`} download><Download size={13} /> {a.name}</a>)}
        </section>
      )}
      <div className="messages">
        {query.data?.messages.map((m: any) => (
          <article key={m.id} className="message">
            <span className="avatar avatar-sm" aria-hidden="true">{initials(m.author)}</span>
            <div>
              <div className="message-head"><strong>{m.author}</strong> <time className="muted">{formatDateTime(m.date)}</time></div>
              {m.body && <div className={`pre-wrap ${m.kind === "tracking" ? "muted" : ""}`}>{m.body}</div>}
              {m.tracking.map((c: any, i: number) => <div key={i} className="tracking">{c.old || "(empty)"} <ArrowRight size={11} aria-label="changed to" /> <b>{c.new || "(empty)"}</b> <span className="muted">({c.field})</span></div>)}
            </div>
          </article>
        ))}
      </div>
      {scheduling && <ScheduleActivity model={model} id={id} onClose={() => setScheduling(false)} onDone={() => query.refetch()} />}
    </aside>
  );
}

function ScheduleActivity({ model, id, onClose, onDone }: { model: string; id: number; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("todo");
  const [due, setDue] = useState(localToday());
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/api/mail/${model}/${id}/activities`, { name, kind, date_deadline: due });
      onDone();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal title="Schedule an activity" onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        <label className="stack-field"><span>Summary</span><input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="stack-field"><span>Type</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="todo">To-Do</option><option value="call">Call</option><option value="email">Email</option><option value="meeting">Meeting</option></select></label>
        <label className="stack-field"><span>Due date</span><input className="input" type="date" required value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <div className="modal-footer"><button className="btn btn-primary">Schedule</button><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}
