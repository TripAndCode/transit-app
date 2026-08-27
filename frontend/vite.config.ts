import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // React Compiler (v1.0) auto-memoizes components — manual useMemo/useCallback
  // are now only needed where the compiler bails out (see eslint react-hooks
  // compiler diagnostics).
  plugins: [react({ babel: { plugins: ["babel-plugin-react-compiler"] } })],
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
    // Emitted to dist/.vite/manifest.json — scripts/check-entry-chunk.mjs
    // reads it to confirm the entry chunk's static import graph never
    // pulls in maplibre-gl (CLAUDE.md: "keep MapLibre out of the entry
    // chunk").
    manifest: true,
  },
});
