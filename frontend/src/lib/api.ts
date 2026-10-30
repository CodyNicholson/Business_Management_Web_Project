export type Rec = Record<string, any>;

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "business-suite" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, credentials: "same-origin", body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : `The request failed (${res.status}).`;
    if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(detail, res.status);
  }
  return data as T;
}

export const api = {
  get: <T = any>(path: string) => request<T>("GET", path),
  post: <T = any>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  patch: <T = any>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
  upload: <T = any>(path: string, form: FormData) => request<T>("POST", path, undefined, form),
};

export function qs(params: Record<string, unknown>): string {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    out.set(k, typeof v === "string" ? v : typeof v === "number" ? String(v) : JSON.stringify(v));
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

export type FieldMeta = {
  name: string; type: string; label: string; required: boolean; readonly: boolean; selection: [string, string][] | null;
  comodel: string | null; domain: any[] | null; onchange: boolean; help: string; columns: string[] | null;
};
export type ActionMeta = { key: string; label: string; params: ParamMeta[] | null; confirm: string | null };
export type ParamMeta = { name: string; label: string; type: string; required?: boolean; comodel?: string; domain?: any[]; default_field?: string; default_today?: boolean };
export type ModelMeta = {
  name: string; label: string; rec_name: string; mail: boolean; locked_when: any[] | null; order: string;
  access: { r: boolean; w: boolean; c: boolean; d: boolean };
  fields: Record<string, FieldMeta>; views: Record<string, any>; actions: Record<string, ActionMeta>;
};
export type MenuItem = { key: string; label: string; model: string | null; views: string[]; domain: any[]; context: Rec; client: string | null; children: MenuItem[] | null };
export type AppMeta = { key: string; name: string; icon: string; color: string; menus: MenuItem[] };
export type Bootstrap = {
  user: { id: number; name: string; email: string; is_admin: boolean; groups: string[] };
  apps: AppMeta[]; activities_due: number; company: Rec;
};
