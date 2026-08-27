#!/usr/bin/env node
// Enforces CLAUDE.md's "keep MapLibre out of the entry chunk" rule. Run
// after `npm run build` / `npm run build:bundle` (needs build.manifest:
// true in vite.config.ts).
//
// For every entry (isEntry: true) in the manifest, walks its STATIC
// import graph only (manifest "imports", never "dynamicImports" — those
// are the lazy-loaded chunks like MapTab this rule wants to stay
// separate) and fails if any statically-reachable chunk's built JS or CSS
// contains a MapLibre marker, or if the summed size of that whole static
// closure (JS + CSS) exceeds a documented budget. Budgeting only
// entry.file (not the closure total) would let a `manualChunks` vendor
// split move bytes into a separately-counted, still-statically-imported
// chunk while the entry file itself stayed small.
//
// DIST_DIR can be overridden via CHECK_ENTRY_CHUNK_DIST_DIR (used by
// tests/frontend/check_entry_chunk.test.mjs to run against a fixture
// dist/ without a real Vite build).

import { readFileSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_DIR = process.env.CHECK_ENTRY_CHUNK_DIST_DIR
  ? resolve(process.env.CHECK_ENTRY_CHUNK_DIST_DIR)
  : join(FRONTEND_DIR, "dist");
const MANIFEST_PATH = join(DIST_DIR, ".vite", "manifest.json");

// getRTLTextPluginStatus is a MapLibre-internal public method name — it
// survives minification (property/method names aren't mangled by
// default), unlike an arbitrary local variable, and unlike the CSS class
// prefix below it can't collide with app code that merely references
// MapLibre's CSS classes (e.g. components/MapPopupHTML.ts).
const JS_MARKER = "getRTLTextPluginStatus";
// MapLibre generates this class on its own root canvas element; it's part
// of the library's actual stylesheet, not something app code would
// plausibly hand-author. A bare "maplibregl-" prefix is NOT safe here —
// this repo's own src/styles/global.css legitimately themes MapLibre
// popups (.maplibregl-popup-content etc.) with a handful of override
// rules, which would false-positive a prefix-only match every time.
const CSS_MARKER = "maplibregl-canvas";

// Budget covers the whole static closure (JS + CSS), not a fixed
// "current measured size" — it's headroom, not a baseline to keep in
// sync by hand. MapLibre alone adds ~800 KiB, so 600 KiB catches a
// MapLibre-scale regression well before it would fit.
const STATIC_CLOSURE_BUDGET_BYTES = 600 * 1024;

function loadManifest() {
  let raw;
  try {
    raw = readFileSync(MANIFEST_PATH, "utf8");
  } catch (err) {
    console.error(
      `check-entry-chunk: could not read ${MANIFEST_PATH} (${err.message}). ` +
        "Did the build run with build.manifest: true in vite.config.ts?",
    );
    process.exit(1);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    console.error(`check-entry-chunk: ${MANIFEST_PATH} is not valid JSON (${err.message}).`);
    process.exit(1);
  }
}

function findEntries(manifest) {
  const entries = Object.entries(manifest).filter(([, node]) => node.isEntry);
  if (entries.length === 0) {
    console.error("check-entry-chunk: no entry (isEntry: true) found in the manifest.");
    process.exit(1);
  }
  return entries;
}

// Manifest keys are the chunk's own source-relative path (e.g.
// "index.html", "src/tabs/MapTab.tsx"); "imports" lists other keys, not
// output filenames, so this walk stays in that key-space throughout.
function staticImportClosure(manifest, startKey) {
  const seen = new Set();
  const stack = [startKey];
  while (stack.length) {
    const key = stack.pop();
    if (seen.has(key)) continue;
    seen.add(key);
    const node = manifest[key];
    if (!node) continue;
    for (const imp of node.imports ?? []) {
      stack.push(imp);
    }
    // Deliberately NOT walking dynamicImports — those are the lazy-loaded
    // chunks (MapTab included) this rule wants to stay excluded.
  }
  return seen;
}

function readTextOrNull(filePath) {
  try {
    return readFileSync(filePath, "utf8");
  } catch {
    return null;
  }
}

function sizeOrNull(filePath) {
  try {
    return statSync(filePath).size;
  } catch {
    return null;
  }
}

const manifest = loadManifest();
const entries = findEntries(manifest);

let failed = false;
let markerSeenAnywhereInDist = false;

// Sanity check the marker choice itself against the WHOLE manifest (not
// just static closures) — MapLibre is still expected to ship somewhere
// (MapTab's lazy chunk), so if the marker is nowhere in dist/ at all,
// this check has silently stopped meaning anything (wrong marker, a
// minifier change, or maplibre-gl no longer being used) and that's worth
// failing loudly on rather than a quiet, permanent pass.
for (const node of Object.values(manifest)) {
  if (!node.file) continue;
  const content = readTextOrNull(join(DIST_DIR, node.file));
  if (content && content.includes(JS_MARKER)) {
    markerSeenAnywhereInDist = true;
    break;
  }
}

for (const [entryKey] of entries) {
  const closureKeys = staticImportClosure(manifest, entryKey);
  let closureTotalBytes = 0;

  for (const key of closureKeys) {
    const node = manifest[key];
    if (!node) continue;

    if (node.file) {
      const filePath = join(DIST_DIR, node.file);
      const content = readTextOrNull(filePath);
      if (content === null) {
        console.error(`check-entry-chunk: FAIL — could not read chunk "${node.file}" (statically reachable from "${entryKey}").`);
        failed = true;
      } else {
        if (content.includes(JS_MARKER)) {
          console.error(
            `check-entry-chunk: FAIL — "${node.file}" (statically reachable from entry "${entryKey}") contains MapLibre. ` +
              "MapLibre must only be reached via a dynamic import (React.lazy), never a static one.",
          );
          failed = true;
        }
        const size = sizeOrNull(filePath);
        if (size === null) {
          console.error(`check-entry-chunk: FAIL — could not stat chunk "${node.file}".`);
          failed = true;
        } else {
          closureTotalBytes += size;
        }
      }
    }

    for (const cssFile of node.css ?? []) {
      const cssPath = join(DIST_DIR, cssFile);
      const content = readTextOrNull(cssPath);
      if (content === null) {
        console.error(`check-entry-chunk: FAIL — could not read stylesheet "${cssFile}" (statically reachable from "${entryKey}").`);
        failed = true;
      } else {
        if (content.includes(CSS_MARKER)) {
          console.error(
            `check-entry-chunk: FAIL — "${cssFile}" (statically reachable from entry "${entryKey}") contains MapLibre's stylesheet. ` +
              "Import maplibre-gl's CSS from MapTab (or another lazy-loaded module), not from a static/entry path.",
          );
          failed = true;
        }
        const size = sizeOrNull(cssPath);
        if (size === null) {
          console.error(`check-entry-chunk: FAIL — could not stat stylesheet "${cssFile}".`);
          failed = true;
        } else {
          closureTotalBytes += size;
        }
      }
    }
  }

  console.log(
    `check-entry-chunk: entry "${entryKey}" static closure (JS+CSS) is ${(closureTotalBytes / 1024).toFixed(1)} KiB ` +
      `across ${closureKeys.size} chunk(s).`,
  );
  if (closureTotalBytes > STATIC_CLOSURE_BUDGET_BYTES) {
    console.error(
      `check-entry-chunk: FAIL — entry "${entryKey}" static closure is ${(closureTotalBytes / 1024).toFixed(1)} KiB, ` +
        `over the ${(STATIC_CLOSURE_BUDGET_BYTES / 1024).toFixed(0)} KiB budget.`,
    );
    failed = true;
  }
}

if (!markerSeenAnywhereInDist) {
  console.error(
    `check-entry-chunk: FAIL — the "${JS_MARKER}" marker was not found anywhere in ${DIST_DIR}. ` +
      "This check is meant to catch MapLibre in the entry chunk by finding a MapLibre-internal " +
      "marker string in the build output; if MapLibre is still a dependency, either the marker no " +
      "longer survives minification or something else changed — investigate before trusting this " +
      "check's result. If maplibre-gl was intentionally removed, delete this check instead.",
  );
  failed = true;
}

if (failed) {
  process.exit(1);
}
console.log("check-entry-chunk: OK — MapLibre stays out of every entry's static closure, size within budget.");
