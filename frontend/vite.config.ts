import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Aplikacja wisi pod prefiksem (/claude-usage/), a Apache ten prefiks OBCINA — backend
// widzi tylko /api/... i /assets/... Dlatego `base` musi byc publiczna sciezka: adresy
// assetow w index.html sa absolutne i Apache mapuje je z powrotem na korzen kontenera.
//
// Jedna wartosc, trzech konsumentow (base -> import.meta.env.BASE_URL):
// basename routera, prefiks API i favicon. Nie czytaj VITE_BASE_PATH nigdzie indziej.
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- konfiguracja biegnie w Node,
// a projekt nie ciagnie @types/node tylko dla tej jednej linii.
const base = (globalThis as any).process?.env?.VITE_BASE_PATH || "/claude-usage/";

export default defineConfig({
  base,
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    // Dev bez backendu: VITE_MOCKS=1 i dane z makiety. Proxy jest tu dla przypadku,
    // gdyby ktos podniosl backend lokalnie — produkcja nie ma CORS ani portu na hoscie.
    proxy: {
      [`${base}api`]: {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(new RegExp(`^${base}`), "/"),
      },
    },
  },
});
