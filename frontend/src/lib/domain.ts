import type { Rec } from "./api";

export function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Replaces $uid, $today and $<field> tokens in a domain with real values. */
export function substitute(domain: any, ctx: Rec): any {
  if (Array.isArray(domain)) return domain.map((d) => substitute(d, ctx));
  if (typeof domain === "string" && domain.startsWith("$")) {
    const key = domain.slice(1);
    if (key === "today") return localToday();
    const v = ctx[key];
    return Array.isArray(v) ? v[0] ?? false : v ?? false;
  }
  return domain;
}

function plain(v: any): any {
  return Array.isArray(v) ? v[0] : v;
}

/** Evaluates view conditions: every [field, op, value] leaf must match. */
export function matches(cond: any[] | null | undefined, values: Rec): boolean {
  if (!cond) return true;
  return cond.every(([field, op, expected]) => {
    const raw = plain(values[field]);
    const v = raw === undefined || raw === null ? false : raw;
    const e = expected === null ? false : expected;
    switch (op) {
      case "=": return v === e;
      case "!=": return v !== e;
      case "in": return e.includes(v);
      case "not in": return !e.includes(v);
      case ">": return v > e;
      case "<": return v < e;
      case ">=": return v >= e;
      case "<=": return v <= e;
      default: return true;
    }
  });
}
