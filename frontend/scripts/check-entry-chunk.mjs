#!/usr/bin/env node
// Enforces CLAUDE.md's "keep MapLibre out of the entry chunk" rule. Run
// after `npm run build` (needs build.manifest: true in vite.config.ts).
//
// Walks the entry's STATIC import graph only (manifest "imports", never
// "dynamicImports" — that's exactly the lazy-loaded chunks like MapTab
// this rule wants to stay separate) and fails if any statically-reachable
// chunk's built JS contains the maplibregl library, or if the entry
// chunk's own size exceeds a documented budget.

import { readFileSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_DIR = join(FRONTEND_DIR, "dist");
const MANIFEST_PATH = join(DIST_DIR, ".vite", "manifest.json");

// Current entry chunk is ~478 kB minified; 600 kB gives real headroom for
// normal growth while still catching a maplibre-scale regression (MapLibre
// alone adds ~800 kB).
const ENTRY_SIZE_BUDGET_BYTES = 600 * 1024;

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
  return JSON.parse(raw);
}

function findEntry(manifest) {
  const entry = Object.values(manifest).find((v) => v.isEntry);
  if (!entry) {
    console.error("check-entry-chunk: no entry (isEntry: true) found in the manifest.");
    process.exit(1);
  }
  return entry;
}

// manifest keys are the chunk's own source-relative path (e.g.
// "index.html", "src/tabs/MapTab.tsx") — "imports" lists other keys, not
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

const manifest = loadManifest();
const entry = findEntry(manifest);
const closureKeys = staticImportClosure(manifest, Object.keys(manifest).find((k) => manifest[k] === entry));

let failed = false;

for (const key of closureKeys) {
  const node = manifest[key];
  if (!node?.file) continue;
  const filePath = join(DIST_DIR, node.file);
  let content;
  try {
    content = readFileSync(filePath, "utf8");
  } catch {
    continue;
  }
  if (content.includes("maplibregl")) {
    console.error(
      `check-entry-chunk: FAIL — "${node.file}" (statically reachable from the entry) contains maplibregl. ` +
        "MapLibre must only be reached via a dynamic import (React.lazy), never a static one.",
    );
    failed = true;
  }
}

const entrySize = statSync(join(DIST_DIR, entry.file)).size;
console.log(`check-entry-chunk: entry chunk "${entry.file}" is ${(entrySize / 1024).toFixed(1)} kB.`);
if (entrySize > ENTRY_SIZE_BUDGET_BYTES) {
  console.error(
    `check-entry-chunk: FAIL — entry chunk is ${(entrySize / 1024).toFixed(1)} kB, over the ` +
      `${(ENTRY_SIZE_BUDGET_BYTES / 1024).toFixed(0)} kB budget.`,
  );
  failed = true;
}

if (failed) {
  process.exit(1);
}
console.log("check-entry-chunk: OK — MapLibre stays out of the entry chunk, size within budget.");
