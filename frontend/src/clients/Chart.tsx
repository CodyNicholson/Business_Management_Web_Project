import { useRef, useState } from "react";
import { formatCompactMoney, formatMoney, formatQty } from "../lib/format";

export type Series = { name: string; values: number[] };
const WIDTH = 760;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const n = v / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
}

type Tip = { x: number; y: number; label: string; rows: { name: string; value: string; slot: number }[] };

function Tooltip({ tip }: { tip: Tip }) {
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="status">
      <strong>{tip.label}</strong>
      {tip.rows.map((r) => <div key={r.name} className="chart-tip-row"><span className={`swatch series-${r.slot}`} /> <span>{r.name}</span> <b>{r.value}</b></div>)}
    </div>
  );
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return <div className="chart-legend">{series.map((s, i) => <span key={s.name}><span className={`swatch series-${i + 1}`} /> {s.name}</span>)}</div>;
}

/** Bars from a single baseline, thin marks, one axis. Hover any band for its values. */
export function ColumnChart({ labels, series, money, kind = "bar" }: { labels: string[]; series: Series[]; money?: boolean; kind?: "bar" | "line" }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const fmt = money ? formatMoney : formatQty;
  const axisFmt = money ? formatCompactMoney : formatQty;
  const height = 300;
  const m = { top: 14, right: 12, bottom: 44, left: 62 };
  const plotW = WIDTH - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const all = series.flatMap((s) => s.values);
  const maxV = niceMax(Math.max(0, ...all));
  const minV = Math.min(0, ...all);
  const lower = minV < 0 ? -niceMax(-minV) : 0;
  const y = (v: number) => m.top + plotH - ((v - lower) / (maxV - lower)) * plotH;
  const band = plotW / Math.max(labels.length, 1);
  const barW = Math.min(24, (band * 0.7 - 2 * (series.length - 1)) / series.length);
  const ticks = Array.from({ length: 5 }, (_, i) => lower + ((maxV - lower) * i) / 4);
  const skip = Math.ceil(labels.length / 12);
  const barPath = (x: number, top: number, w: number, base: number) => {
    const r = Math.min(4, w / 2, Math.abs(base - top));
    return top <= base
      ? `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${base}Z`
      : `M${x},${base}V${top - r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top - r}V${base}Z`;
  };
  function show(i: number, e: React.MouseEvent) {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top - 8, label: labels[i], rows: series.map((s, si) => ({ name: s.name, value: fmt(s.values[i]), slot: si + 1 })) });
  }
  return (
    <div className="chart" ref={box} onMouseLeave={() => setTip(null)}>
      <Legend series={series} />
      <svg viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label={`${series.map((s) => s.name).join(" and ")} across ${labels.length} categories. A table view is available.`}>
        {ticks.map((t) => <g key={t}><line className="chart-grid" x1={m.left} x2={WIDTH - m.right} y1={y(t)} y2={y(t)} /><text className="chart-axis" x={m.left - 8} y={y(t) + 4} textAnchor="end">{axisFmt(t)}</text></g>)}
        {labels.map((l, i) => i % skip === 0 && <text key={`${l}-${i}`} className="chart-axis" x={m.left + band * (i + 0.5)} y={height - m.bottom + 18} textAnchor="middle">{l.length > 14 ? `${l.slice(0, 13)}…` : l}</text>)}
        {kind === "bar" && series.map((s, si) => s.values.map((v, i) => {
          const groupW = series.length * barW + (series.length - 1) * 2;
          const x = m.left + band * i + (band - groupW) / 2 + si * (barW + 2);
          return v === 0 ? null : <path key={`${si}-${i}`} className={`series-fill-${si + 1}`} d={barPath(x, y(v), barW, y(0))} />;
        }))}
        {kind === "line" && series.map((s, si) => (
          <g key={s.name}>
            <polyline className={`chart-line series-stroke-${si + 1}`} points={s.values.map((v, i) => `${m.left + band * (i + 0.5)},${y(v)}`).join(" ")} />
            {s.values.map((v, i) => <circle key={i} className={`chart-dot series-fill-${si + 1}`} cx={m.left + band * (i + 0.5)} cy={y(v)} r={4} />)}
          </g>
        ))}
        {labels.map((l, i) => <rect key={`hit-${l}-${i}`} x={m.left + band * i} y={m.top} width={band} height={plotH} className="chart-hit" onMouseMove={(e) => show(i, e)} />)}
      </svg>
      {tip && <Tooltip tip={tip} />}
    </div>
  );
}

export function BarListChart({ labels, series, money }: { labels: string[]; series: Series[]; money?: boolean }) {
  const fmt = money ? formatMoney : formatQty;
  const values = series[0]?.values ?? [];
  const max = Math.max(...values, 1);
  return (
    <div className="bar-list">
      {labels.map((l, i) => (
        <div key={l} className="bar-list-row">
          <span className="bar-list-label">{l}</span>
          <span className="bar-list-track"><span className="bar-list-fill series-bg-1" style={{ width: `${Math.max((values[i] / max) * 100, values[i] > 0 ? 1 : 0)}%` }} /></span>
          <span className="bar-list-value">{fmt(values[i])}</span>
        </div>
      ))}
    </div>
  );
}

export function ChartTable({ labels, series, money, head }: { labels: string[]; series: Series[]; money?: boolean; head: string }) {
  const fmt = money ? formatMoney : formatQty;
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead><tr><th>{head}</th>{series.map((s) => <th key={s.name} className="num">{s.name}</th>)}</tr></thead>
        <tbody>{labels.map((l, i) => <tr key={`${l}-${i}`}><td>{l}</td>{series.map((s) => <td key={s.name} className="num">{fmt(s.values[i])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
