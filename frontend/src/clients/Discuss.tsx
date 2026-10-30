import { useQuery } from "@tanstack/react-query";
import { Hash, Lock, Plus, Send, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { formatDateTime, initials } from "../lib/format";
import { useToast } from "../lib/hooks";
import { Modal } from "../views/ActionDialog";

export function Discuss() {
  const toast = useToast();
  const channels = useQuery<any>({ queryKey: ["discuss-channels"], queryFn: () => api.get("/api/discuss/channels") });
  const [active, setActive] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const list: any[] = channels.data?.channels ?? [];
  const channelId = active ?? list[0]?.id ?? null;
  const current = list.find((c) => c.id === channelId);
  const messages = useQuery<any[]>({ queryKey: ["discuss", channelId], enabled: channelId !== null, refetchInterval: 4000, queryFn: () => api.get(`/api/discuss/channels/${channelId}/messages`) });
  const bottom = useRef<HTMLDivElement>(null);
  const count = messages.data?.length ?? 0;
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [count, channelId]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || channelId === null) return;
    setSending(true);
    try {
      await api.post(`/api/discuss/channels/${channelId}/messages`, { body: text });
      setText("");
      await messages.refetch();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSending(false);
    }
  }

  async function openDm(userId: number) {
    try {
      const res = await api.post("/api/discuss/channels", { kind: "dm", user_id: userId });
      await channels.refetch();
      setActive(res.id);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  const iconFor = (kind: string) => (kind === "channel" ? <Hash size={14} /> : kind === "group" ? <Lock size={14} /> : <User size={14} />);
  return (
    <div className="discuss">
      <aside className="discuss-side" aria-label="Channels">
        <div className="discuss-side-head"><h2>Channels</h2><button className="icon-btn" aria-label="New channel" onClick={() => setCreating(true)}><Plus size={16} /></button></div>
        {list.map((c) => <button key={c.id} className={`discuss-channel ${c.id === channelId ? "active" : ""}`} onClick={() => setActive(c.id)}>{iconFor(c.kind)} {c.name}</button>)}
        <div className="discuss-side-head discuss-people"><h2>People</h2></div>
        {channels.data?.users.map(([id, name]: [number, string]) => <button key={id} className="discuss-channel" onClick={() => openDm(id)}><span className="avatar avatar-xs" aria-hidden="true">{initials(name)}</span> {name}</button>)}
      </aside>
      <section className="discuss-main" aria-label={current?.name ?? "Conversation"}>
        <header className="discuss-head"><h1>{current?.name ?? "Discuss"}</h1>{current?.description && <span className="muted">{current.description}</span>}</header>
        <div className="discuss-messages" aria-live="polite">
          {messages.data?.length === 0 && <div className="empty"><p>No messages yet. Say hello.</p></div>}
          {messages.data?.map((m) => (
            <article key={m.id} className="message">
              <span className="avatar avatar-sm" aria-hidden="true">{initials(m.author)}</span>
              <div><div className="message-head"><strong>{m.author}</strong> <time className="muted">{formatDateTime(m.date)}</time></div><div className="pre-wrap">{m.body}</div></div>
            </article>
          ))}
          <div ref={bottom} />
        </div>
        <form className="discuss-input" onSubmit={send}>
          <input className="input" aria-label="Message" placeholder={current ? `Message ${current.name}` : "Select a channel"} value={text} disabled={!current} onChange={(e) => setText(e.target.value)} />
          <button className="btn btn-primary" aria-label="Send" disabled={sending || !text.trim()}><Send size={15} /></button>
        </form>
      </section>
      {creating && <NewChannel onClose={() => setCreating(false)} onCreated={async (id) => { await channels.refetch(); setActive(id); }} />}
    </div>
  );
}

function NewChannel({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("channel");
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = await api.post("/api/discuss/channels", { name, kind });
      onCreated(res.id);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal title="New channel" onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        <label className="stack-field"><span>Name</span><input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="stack-field"><span>Visibility</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="channel">Public channel</option><option value="group">Private group</option></select></label>
        <div className="modal-footer"><button className="btn btn-primary">Create</button><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}
