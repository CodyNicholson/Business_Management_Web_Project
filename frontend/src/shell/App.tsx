import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { setCurrency } from "../lib/format";
import { useBootstrap } from "../lib/hooks";
import { PrintPage } from "../clients/PrintPage";
import { ActionPage } from "../views/ActionPage";
import { Home } from "./Home";
import { Navbar } from "./Navbar";

export function App() {
  const boot = useBootstrap();
  const client = useQueryClient();
  const location = useLocation();

  useEffect(() => {
    const onExpired = () => client.setQueryData(["bootstrap"], null);
    window.addEventListener("session-expired", onExpired);
    return () => window.removeEventListener("session-expired", onExpired);
  }, [client]);

  if (boot.isLoading) return <div className="center-screen"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (boot.error && (boot.error as ApiError).status !== 401) {
    return <div className="center-screen"><div className="empty"><h2>The server could not be reached</h2><p>{(boot.error as Error).message}</p><button className="btn btn-primary" onClick={() => boot.refetch()}>Try again</button></div></div>;
  }
  if (!boot.data) return <LoginPage />;
  setCurrency(boot.data.company.currency_symbol);
  if (location.pathname.startsWith("/print/")) {
    return <Routes><Route path="/print/:kind/:model/:id" element={<PrintPage />} /></Routes>;
  }
  return (
    <div className="app-shell">
      <Navbar />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/a/:app" element={<ActionPage />} />
          <Route path="/a/:app/m/:model" element={<ActionPage />} />
          <Route path="/a/:app/m/:model/:id" element={<ActionPage />} />
          <Route path="/a/:app/:action" element={<ActionPage />} />
          <Route path="/a/:app/:action/:id" element={<ActionPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function LoginPage() {
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("/api/auth/login", { email, password });
      await client.resetQueries();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><img src="/favicon.svg" alt="" width={36} height={36} /><span>Business Suite</span></div>
        <h1>Sign in</h1>
        {error && <div className="banner banner-error" role="alert">{error}</div>}
        <label className="stack-field"><span>Email</span><input className="input" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="stack-field"><span>Password</span><input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn btn-primary btn-block" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </div>
  );
}
