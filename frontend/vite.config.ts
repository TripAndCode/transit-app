import { readdirSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, loadEnv, type Plugin } from "vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import babel from "@rolldown/plugin-babel";

const LOCALE_CHUNKS_PLACEHOLDER = "/*__LOCALE_CHUNKS__*/ {}";

// Fills index.html's locale preload with each language's hashed chunk URL.
// Fails the build when a locale file has no chunk of its own: a static import
// folds that language into a chunk every visitor downloads.
function localeChunkMap(): Plugin {
  let localesDir = "";
  let base = "/";
  return {
    name: "locale-chunk-map",
    apply: "build",
    configResolved(config) {
      localesDir = join(config.root, "src/i18n/locales");
      base = config.base;
    },
    transformIndexHtml: {
      order: "post",
      handler(html, { bundle }) {
        const chunks: Record<string, string> = {};
        for (const output of Object.values(bundle ?? {})) {
          if (output.type !== "chunk" || !output.facadeModuleId?.startsWith(`${localesDir}/`)) continue;
          chunks[output.facadeModuleId.slice(localesDir.length + 1).replace(/\.json$/, "")] = base + output.fileName;
        }
        const missing = readdirSync(localesDir)
          .filter((file) => file.endsWith(".json"))
          .map((file) => file.replace(/\.json$/, ""))
          .filter((locale) => !(locale in chunks));
        if (missing.length > 0) {
          throw new Error(
            `locale-chunk-map: no chunk of its own for ${missing.join(", ")}. Load locale JSON only ` +
              "through the dynamic loaders in src/i18n/index.ts, never with a static import.",
          );
        }
        if (html.split(LOCALE_CHUNKS_PLACEHOLDER).length !== 2) {
          throw new Error(`locale-chunk-map: index.html must contain ${LOCALE_CHUNKS_PLACEHOLDER} exactly once.`);
        }
        return html.replace(LOCALE_CHUNKS_PLACEHOLDER, () => JSON.stringify(chunks));
      },
    },
  };
}

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
      // Backend lives under /api/* and /health. Anything else is owned by
      // the SPA — including /agencies/:id/map, which used to break in dev
      // because the proxy intercepted /agencies/* and forwarded it to
      // FastAPI. Agency CRUD now lives at /api/agencies, so the proxy is
      // a clean two-line setup, with the target overridable per checkout
      // so worktrees don't silently share whichever backend holds 8000.
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
      // pulls in maplibre-gl (CLAUDE.md: "keep MapLibre out of the entry
      // chunk").
      manifest: true,
    },
  };
});
