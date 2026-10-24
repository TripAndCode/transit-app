import { defineConfig, loadEnv } from "vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import babel from "@rolldown/plugin-babel";
import { localeChunkMap } from "./scripts/localeChunkMap.mjs";
import { servedRoots } from "./scripts/servedRoots.mjs";

export default defineConfig(({ mode }) => {
  // loadEnv (not process.env) so a per-checkout .env.local can set this —
  // Vite's config file runs before it populates process.env itself.
  const API_TARGET =
    loadEnv(mode, process.cwd(), "VITE_").VITE_API_TARGET ??
    "http://localhost:8000";

  return {
    // React Compiler (v1.0) auto-memoizes components. Manual useMemo/useCallback/
    // React.memo are banned as a hard ESLint error (see eslint.config.js) — a
    // compiler bailout should be fixed at the source, not worked around with
    // manual memoization.
    plugins: [react(), babel({ presets: [reactCompilerPreset()] }), localeChunkMap()],
    server: {
      port: 5173,
      fs: { allow: servedRoots() },
      // Backend lives under /api/* and /health. Anything else is owned by
      // the SPA — including every /agencies/:id/* screen, which must never
      // reach FastAPI (agency CRUD lives at /api/agencies). The target is
      // overridable per checkout so worktrees don't silently share
      // whichever backend holds 8000.
      proxy: {
        "/api": API_TARGET,
        "/health": API_TARGET,
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
      // Emitted to dist/.vite/manifest.json — scripts/check-entry-chunk.mjs
      // reads it to confirm the entry chunk's static import graph never
      // pulls in maplibre-gl (AGENTS.md: "keep MapLibre out of the entry
      // chunk").
      manifest: true,
    },
  };
});
