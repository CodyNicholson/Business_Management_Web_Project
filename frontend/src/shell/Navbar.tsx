import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, LayoutGrid, LogOut, KeyRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, type MenuItem } from "../lib/api";
import { formatDate, initials } from "../lib/format";
import { useBootstrap, useToast } from "../lib/hooks";
import { Modal } from "../views/ActionDialog";

function Dropdown({ label, children, align, className, ariaLabel }: { label: ReactNode; children: ReactNode; align?: "right"; className?: string; ariaLabel?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="dropdown" ref={ref}>
      <button className={className ?? "nav-link"} aria-expanded={open} aria-haspopup="menu" aria-label={ariaLabel} onClick={() => setOpen(!open)}>{label}</button>
      {open && <div className={`dropdown-menu ${align === "right" ? "dropdown-right" : ""}`} onClick={() => setOpen(false)}>{children}</div>}
    </div>
  );
}

export { Dropdown };

export function Navbar() {
  const { data } = useBootstrap();
  const location = useLocation();
  const appKey = location.pathname.match(/^\/a\/([^/]+)/)?.[1];
  const app = data?.apps.find((a) => a.key === appKey);
  return (
    <header className="navbar">
      <Link to="/" className="nav-home" aria-label="All apps"><LayoutGrid size={18} /></Link>
      {app && <Link to={`/a/${app.key}`} className="nav-app-name">{app.name}</Link>}
      <nav className="nav-menus" aria-label="App menu">
        {app?.menus.map((m) => <MenuEntry key={m.key} app={app.key} item={m} />)}
      </nav>
      <div className="nav-right">
        <ActivitiesMenu />
        <UserMenu />
      </div>
    </header>
  );
}

function MenuEntry({ app, item }: { app: string; item: MenuItem }) {
  if (item.children) {
    return (
      <Dropdown label={item.label}>
        {item.children.map((c) => <Link key={c.key} className="dropdown-item" to={`/a/${app}/${c.key}`}>{c.label}</Link>)}
      </Dropdown>
    );
  }
  return <NavLink to={`/a/${app}/${item.key}`} className="nav-link">{item.label}</NavLink>;
}

function ActivitiesMenu() {
  const { data } = useBootstrap();
  const navigate = useNavigate();
  const client = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const activities = useQuery<any[]>({ queryKey: ["my-activities"], queryFn: () => api.get("/api/activities/mine"), enabled: open });
  const due = data?.activities_due ?? 0;
  async function done(id: number) {
    try {
      await api.post(`/api/activities/${id}/done`);
      await Promise.all([activities.refetch(), client.invalidateQueries({ queryKey: ["bootstrap"] })]);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  return (
    <div className="dropdown">
      <button className="nav-link nav-icon" aria-label={`Activities, ${due} due`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Clock size={17} />{due > 0 && <span className="nav-badge">{due}</span>}
      </button>
      {open && (
        <div className="dropdown-menu dropdown-right activity-menu">
          <div className="dropdown-title">My Activities</div>
          {activities.isLoading && <div className="muted pad">Loading...</div>}
          {activities.data?.length === 0 && <div className="muted pad">Nothing scheduled.</div>}
          {activities.data?.map((a) => (
            <div key={a.id} className="activity-row">
              <button className="link-btn" onClick={() => { if (a.res_model) { setOpen(false); navigate(`/a/contacts/m/${a.res_model}/${a.res_id}`); } }}>
                <strong>{a.name}</strong>
                <span className="muted">{a.model_label} · {formatDate(a.date_deadline)}</span>
              </button>
              <button className="btn btn-sm" onClick={() => done(a.id)}>Done</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UserMenu() {
  const { data } = useBootstrap();
  const client = useQueryClient();
  const [changing, setChanging] = useState(false);
  async function logout() {
    await api.post("/api/auth/logout");
    client.clear();
    client.setQueryData(["bootstrap"], null);
    window.location.href = "/";
  }
  return (
    <>
      <Dropdown align="right" ariaLabel="Account menu" className="nav-link nav-user" label={<><span className="avatar">{initials(data?.user.name ?? "")}</span><span className="nav-user-name">{data?.user.name}</span></>}>
        <div className="dropdown-title">{data?.user.email}</div>
        <button className="dropdown-item" onClick={() => setChanging(true)}><KeyRound size={14} /> Change password</button>
        <button className="dropdown-item" onClick={logout}><LogOut size={14} /> Sign out</button>
      </Dropdown>
      {changing && <ChangePassword onClose={() => setChanging(false)} />}
    </>
  );
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/auth/change_password", { current, new: next });
      toast("Password changed. Other sessions were signed out.");
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal title="Change password" onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        <label className="stack-field"><span>Current password</span><input className="input" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
        <label className="stack-field"><span>New password (at least 12 characters)</span><input className="input" type="password" autoComplete="new-password" minLength={12} required value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <div className="modal-footer"><button className="btn btn-primary">Change password</button><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}
