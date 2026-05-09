import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Backend lives under /api/* and /health. Anything else is owned by
    // the SPA — including /agencies/:id/map, which used to break in dev
    // because the proxy intercepted /agencies/* and forwarded it to
    // FastAPI. Agency CRUD now lives at /api/agencies, so the proxy is
    // a clean two-line setup.
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
