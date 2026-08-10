import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

// Inter self-hosted — the mockup pulled it from Google Fonts, but this tool has to work
// without querying a CDN. Two weights, because Nocturne uses 400 and 500 (headings get no heavier).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";

import "./styles/nocturne.css";
import "./styles/theme.css";
import "./styles/app.css";

import { App } from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,
      // A network error is retried once; a retry loop against a dead backend only clutters
      // the logs and delays the message on screen.
      retry: 1,
    },
  },
});

// basename from the same value as `base` in vite.config.ts (Apache strips the prefix,
// so the router has to know about it to build correct addresses in the address bar).
const basename = import.meta.env.BASE_URL.replace(/\/$/, "");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
