import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./shell/App";
import { ToastProvider } from "./lib/hooks";
import "./styles/global.scss";

const client = new QueryClient({
  mutationCache: new MutationCache(),
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: (count, err: any) => count < 1 && !(err?.status >= 400 && err?.status < 500) } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
