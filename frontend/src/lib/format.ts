import type { FieldMeta } from "./api";

let currencySymbol = "$";
export function setCurrency(symbol: string) {
  currencySymbol = symbol || "$";
}

const NUM = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const QTY = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
const COMPACT = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export const formatMoney = (v: number | null | undefined) => `${(v ?? 0) < 0 ? "-" : ""}${currencySymbol}${NUM.format(Math.abs(v ?? 0))}`;
export const formatCompactMoney = (v: number) => `${v < 0 ? "-" : ""}${currencySymbol}${COMPACT.format(Math.abs(v))}`;
export const formatQty = (v: number | null | undefined) => QTY.format(v ?? 0);

export function formatDate(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v.length === 10 ? `${v}T12:00:00` : v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatValue(f: FieldMeta | undefined, v: any): string {
  if (!f) return v == null ? "" : String(v);
  switch (f.type) {
    case "many2one": case "file": return v ? String(v[1]) : "";
    case "many2many": return (v ?? []).map((x: any) => x[1]).join(", ");
    case "money": return v == null ? "" : formatMoney(v);
    case "float": return v == null ? "" : formatQty(v);
    case "int": return v == null ? "" : String(v);
    case "date": return formatDate(v);
    case "datetime": return formatDateTime(v);
    case "selection": return (f.selection ?? []).find((o) => o[0] === v)?.[1] ?? (v ?? "");
    case "bool": return v ? "Yes" : "No";
    default: return v == null ? "" : String(v);
  }
}

/** A group-by key from read_group: [id, label], a date, or a plain value. */
export function groupLabel(v: any, spec: string): string {
  if (v === null || v === undefined || v === false) return "None";
  if (Array.isArray(v)) return String(v[1]);
  const gran = spec.split(":")[1];
  if (gran && typeof v === "string") {
    const d = new Date(`${v.slice(0, 10)}T12:00:00`);
    if (gran === "month") return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
    if (gran === "year") return String(d.getFullYear());
    if (gran === "quarter") return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
    return formatDate(v);
  }
  return String(v);
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}

const TONES: Record<string, string> = {
  draft: "neutral", new: "neutral", sent: "info", confirm: "warn", confirmed: "info", assigned: "info", ready: "info", opened: "info", open: "info", progress: "info", queue: "warn", submitted: "warn", pending: "neutral",
  posted: "good", sale: "good", purchase: "good", done: "good", paid: "good", validate: "good", won: "good", approved: "good", active: "good", invoiced: "good", billed: "good", present: "good", sold: "neutral", announced: "good", booked: "info",
  partial: "warn", not_paid: "warn", to_invoice: "warn", to_bill: "warn", repair: "warn",
  cancel: "bad", refuse: "bad", refused: "bad", lost: "bad", failed: "bad", closed: "neutral", ended: "neutral", no: "neutral", na: "neutral", absent: "neutral",
};
export const toneOf = (value: string) => TONES[value] ?? "neutral";
