import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { formatMoney } from "../lib/format";
import { BarListChart, ChartTable, ColumnChart } from "./Chart";

const monthLabel = (ym: string) => new Date(`${ym}-15T12:00:00`).toLocaleDateString(undefined, { month: "short", year: "2-digit" });

export function Dashboard() {
  const query = useQuery<any>({ queryKey: ["dashboard"], queryFn: () => api.get("/api/dashboard") });
  if (query.isLoading) return <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (query.error) return <div className="empty"><h2>The dashboard could not be loaded</h2><p>{(query.error as Error).message}</p></div>;
  return (
    <div className="client-page">
      <h1 className="page-title">My Dashboard</h1>
      <div className="tiles">
        {query.data.tiles.map((t: any) => (
          <Link key={t.label} className="tile" to={`/a/${t.app}/${t.action}`}>
            <span className="tile-label">{t.label}</span>
            <span className="tile-value">{t.money ? formatMoney(t.value) : t.value}</span>
            {t.sub !== undefined && <span className="muted">{formatMoney(t.sub)} total</span>}
          </Link>
        ))}
      </div>
      <div className="chart-grid-2">{query.data.charts.map((c: any) => <ChartCard key={c.key} chart={c} />)}</div>
    </div>
  );
}

function ChartCard({ chart }: { chart: any }) {
  const [table, setTable] = useState(false);
  const labels = chart.kind === "bar" ? chart.labels.map(monthLabel) : chart.labels;
  return (
    <section className="card chart-card">
      <header><h2>{chart.title}</h2><button className="link-btn" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button></header>
      {table ? <ChartTable labels={labels} series={chart.series} money={chart.money} head={chart.kind === "bar" ? "Month" : "Stage"} />
        : chart.kind === "bar" ? <ColumnChart labels={labels} series={chart.series} money={chart.money} />
        : <BarListChart labels={labels} series={chart.series} money={chart.money} />}
    </section>
  );
}
