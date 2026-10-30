import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogIn, LogOut } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";

export function Attendance() {
  const client = useQueryClient();
  const query = useQuery<any>({ queryKey: ["attendance"], queryFn: () => api.get("/api/attendance"), refetchInterval: 60_000 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    setError("");
    try {
      await api.post("/api/attendance/toggle");
      await client.invalidateQueries({ queryKey: ["attendance"] });
      await client.invalidateQueries({ queryKey: ["records", "hr.attendance"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (query.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (query.error) return <div className="empty"><p>{(query.error as Error).message}</p></div>;
  const s = query.data;
  if (!s.employee) return <div className="empty"><h2>No employee record</h2><p>Your user is not linked to an employee. Ask HR to link it.</p></div>;
  return (
    <div className="attendance card">
      <h1>{s.employee}</h1>
      <p className="muted">{s.checked_in ? `Checked in since ${formatDateTime(s.since)}` : "You are checked out."}</p>
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <button className={`attendance-btn ${s.checked_in ? "out" : "in"}`} disabled={busy} onClick={toggle}>
        {s.checked_in ? <LogOut size={30} /> : <LogIn size={30} />}
        <span>{s.checked_in ? "Check out" : "Check in"}</span>
      </button>
      <p>Today: <b>{s.today_hours.toFixed(2)}</b> hours</p>
    </div>
  );
}
