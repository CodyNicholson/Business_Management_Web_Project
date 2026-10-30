import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { api, type Bootstrap, type ModelMeta } from "./api";

export function useBootstrap() {
  return useQuery<Bootstrap>({ queryKey: ["bootstrap"], queryFn: () => api.get("/api/meta/bootstrap"), staleTime: 60_000, retry: false });
}

export function useModelMeta(model: string | null | undefined) {
  return useQuery<ModelMeta>({ queryKey: ["meta", model], queryFn: () => api.get(`/api/meta/models/${model}`), enabled: !!model, staleTime: 300_000 });
}

type Toast = { id: number; kind: "ok" | "error"; message: string };
const ToastContext = createContext<(message: string, kind?: Toast["kind"]) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);
  const remove = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback((message: string, kind: Toast["kind"] = "ok") => {
    const id = ++counter.current;
    setToasts((t) => [...t, { id, kind, message }]);
    window.setTimeout(() => remove(id), kind === "error" ? 8000 : 3500);
  }, [remove]);
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <span>{t.message}</span>
            <button className="icon-btn" aria-label="Dismiss" onClick={() => remove(t.id)}><X size={14} /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
