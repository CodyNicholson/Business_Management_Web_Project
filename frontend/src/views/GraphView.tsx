import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ModelMeta, type Rec } from "../lib/api";
import { ChartTable, ColumnChart } from "../clients/Chart";
import { groupLabel } from "../lib/format";

export function GraphView({ meta, domain, groupBy }: { meta: ModelMeta; domain: any[]; groupBy: string | null }) {
  const spec = meta.views.graph;
  const group: string = groupBy ?? spec.group_by;
  const measures: string[] = (spec.measures ?? []).filter((m: string) => meta.fields[m]);
  const [measure, setMeasure] = useState<string>(measures[0] ?? "__count");
  const [kind, setKind] = useState<"bar" | "line" | "table">(spec.kind ?? "bar");
  const query = useQuery<{ groups: Rec[] }>({
    queryKey: ["records", meta.name, "graph", domain, group, measure],
    queryFn: () => api.post(`/api/models/${meta.name}/read_group`, { domain, group_by: [group], measures: measure === "__count" ? [] : [measure] }),
  });
  if (query.error) return <div className="empty"><h2>This graph could not be loaded</h2><p>{(query.error as Error).message}</p></div>;
  const groups = query.data?.groups ?? [];
  const labels = groups.map((g) => groupLabel(g[group], group));
  const name = measure === "__count" ? "Count" : meta.fields[measure]?.label ?? measure;
  const series = [{ name, values: groups.map((g) => Number(g[measure] ?? 0)) }];
  const money = measure !== "__count" && meta.fields[measure]?.type === "money";
  const head = meta.fields[group.split(":")[0]]?.label ?? group;
  return (
    <div className="card graph-card">
      <div className="graph-toolbar">
        <label className="inline-field">Measure
          <select className="input input-sm" value={measure} onChange={(e) => setMeasure(e.target.value)}>
            {measures.map((m) => <option key={m} value={m}>{meta.fields[m].label}</option>)}
            <option value="__count">Count</option>
          </select>
        </label>
        <div className="seg" role="group" aria-label="Chart type">
          {(["bar", "line", "table"] as const).map((k) => <button key={k} className={`btn btn-sm ${kind === k ? "btn-active" : ""}`} aria-pressed={kind === k} onClick={() => setKind(k)}>{k[0].toUpperCase() + k.slice(1)}</button>)}
        </div>
        <span className="muted">{name} by {head.toLowerCase()}</span>
      </div>
      {query.isLoading ? <div className="center-pad"><div className="spinner" role="status" aria-label="Loading" /></div>
        : groups.length === 0 ? <div className="empty"><p>No data for this selection.</p></div>
        : kind === "table" ? <ChartTable labels={labels} series={series} money={money} head={head} />
        : <ColumnChart labels={labels} series={series} money={money} kind={kind} />}
    </div>
  );
}
