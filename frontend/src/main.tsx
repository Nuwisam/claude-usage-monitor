import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

// Inter self-hosted — makieta ciagnela go z Google Fonts, ale to narzedzie ma dzialac
// bez odpytywania CDN-a. Dwie wagi, bo Nocturne uzywa 400 i 500 (naglowki nie tyja dalej).
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
      // Blad sieci probujemy raz; petla retry przy padnietym backendzie tylko zasmieca
      // logi i opoznia komunikat na ekranie.
      retry: 1,
    },
  },
});

// basename z tej samej wartosci co `base` w vite.config.ts (Apache obcina prefiks,
// wiec router musi o nim wiedziec, zeby budowac poprawne adresy w pasku).
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
