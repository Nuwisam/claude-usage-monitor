import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The application hangs under a prefix (/claude-usage/), and Apache STRIPS that prefix — the
// backend sees only /api/... and /assets/... That is why `base` must be the public path: the
// asset addresses in index.html are absolute and Apache maps them back to the container root.
//
// One value, three consumers (base -> import.meta.env.BASE_URL):
// the router basename, the API prefix and the favicon. Read VITE_BASE_PATH nowhere else.
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- the config runs in Node,
// and the project does not pull in @types/node just for this one line.
const base = (globalThis as any).process?.env?.VITE_BASE_PATH || "/claude-usage/";

export default defineConfig({
  base,
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    // Dev without a backend: VITE_MOCKS=1 and data from the mockup. The proxy is here in case
    // someone brings a backend up locally — a real deployment has no CORS and no host port.
    proxy: {
      [`${base}api`]: {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(new RegExp(`^${base}`), "/"),
      },
    },
  },
});
