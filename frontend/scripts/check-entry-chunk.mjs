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
// SCOPE BOUNDARY: the manifest walk above only sees chunks Vite itself
// processed as JS/CSS module graph nodes. It does NOT see a hand-authored
// `<script src="...">` or `<link href="...">` tag added directly to
// index.html that points at a file copied verbatim into dist/ (e.g. from
// `public/`) — that file ships in the exact same initial HTML load but
// has no manifest entry at all, so the walk above can't reach it. To
// close that gap, a second, independent pass below scans the BUILT
// dist/index.html's own <script>/<link> tags directly (regardless of
// whether Vite tracked them) for the same MapLibre markers. This is a
// narrow, deliberate bypass to guard against — not something an
// accidental refactor would trigger — but it's real and previously
// undetected.
//
// DIST_DIR can be overridden with --dist-dir <path> (used by
// tests/frontend/check_entry_chunk.test.mjs to run against a fixture
// dist/ without a real Vite build). Deliberately an explicit CLI arg, not
// an env var: an ambient env var (a leftover shell export, a repo
// .envrc, a workflow-level `env:` added later) would silently redirect a
// real run at a fixture dir with no signal, defeating a check that's
// otherwise fail-closed.

import { readFileSync, statSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

function distDirFromArgv() {
  const flagIndex = process.argv.indexOf("--dist-dir");
  if (flagIndex === -1) return null;
  const value = process.argv[flagIndex + 1];
  if (!value) {
    console.error("check-entry-chunk: --dist-dir requires a path argument.");
    process.exit(1);
  }
  return resolve(value);
}

const FRONTEND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_DIR = distDirFromArgv() ?? join(FRONTEND_DIR, "dist");
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

// Matches <script ... src="...">, <link ... href="...">, single- or
// double-quoted, tag attributes in any order/case. Also matches HTML5's
// unquoted attribute-value form (e.g. src=vendor-maplibre.js) — browsers
// execute that identically to a quoted src, so a hand-authored tag using it
// would otherwise sail through this scan unnoticed. This is a deliberately
// simple regex scan (not an HTML parser) — good enough for this repo's
// single, hand-maintained index.html; it is not meant to handle arbitrary
// HTML (e.g. attribute values split across lines, or src set via JS).
// Each regex has two capture groups (quoted, unquoted); exactly one is
// populated per match — see collectHtmlAssetUrls below. Known scope gap: an
// unquoted value immediately followed by a self-closing `/>` with no space
// (e.g. src=/vendor.js/>) folds the trailing `/` into the captured URL; that
// then fails to resolve to a real dist/ file and is silently skipped rather
// than flagged (a narrow authoring style — the common unquoted self-closing
// form has a space before the slash, which resolves correctly).
const SCRIPT_SRC_RE = /<script\b[^>]*\bsrc\s*=\s*(?:["']([^"']+)["']|([^\s"'=<>`]+))[^>]*>/gi;
const LINK_HREF_RE = /<link\b[^>]*\bhref\s*=\s*(?:["']([^"']+)["']|([^\s"'=<>`]+))[^>]*>/gi;

// A tag pointing at another origin (http(s):, protocol-relative //, or a
// non-fetchable scheme like data:/mailto:) can't be a locally-shipped
// MapLibre bundle copied into dist/, so it's out of scope for this check.
// Accepted scope limit: a hand-authored tag using the site's own absolute
// production URL (e.g. https://app.example.com/vendor-maplibre.js) is also
// treated as external and skipped here. That requires hardcoding the real
// deploy origin, which is far more conspicuous in code review than a bare
// relative path — not worth building same-origin URL resolution to catch.
function isExternalOrNonFileUrl(url) {
  return /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(url);
}

// index.html's tags reference paths as served from the site root ("/"),
// which corresponds to DIST_DIR at build output time.
function resolveHtmlAssetPath(url) {
  const withoutQueryOrHash = url.split(/[?#]/)[0];
  const relative = withoutQueryOrHash.startsWith("/") ? withoutQueryOrHash.slice(1) : withoutQueryOrHash;
  return join(DIST_DIR, relative);
}

function collectHtmlAssetUrls(html) {
  const urls = [];
  for (const re of [SCRIPT_SRC_RE, LINK_HREF_RE]) {
    re.lastIndex = 0;
    let match;
    while ((match = re.exec(html))) {
      urls.push(match[1] ?? match[2]);
    }
  }
  return urls;
}

function loadManifest() {
  let raw;
  try {
    raw = readFileSync(MANIFEST_PATH, "utf8");
  } catch (err) {
    console.error(`check-entry-chunk: could not read ${MANIFEST_PATH} (${err.message}).`);
    // frontend/tsconfig.node.json redirects tsc -b's emit away from
    // frontend/ specifically so this file can never exist going forward,
    // but a checkout that ran `tsc -b` before that fix landed can still
    // have one sitting there (gitignored, so `git status` won't show it)
    // — and Vite loads vite.config.js before vite.config.ts if both
    // exist, silently dropping build.manifest: true.
    if (existsSync(join(FRONTEND_DIR, "vite.config.js"))) {
      console.error(
        "check-entry-chunk: found a stale frontend/vite.config.js — Vite loads that " +
          "instead of vite.config.ts, so build.manifest: true never applied. Delete " +
          "frontend/vite.config.js and frontend/vite.config.d.ts and rebuild.",
      );
    } else {
      console.error("check-entry-chunk: did the build run with build.manifest: true in vite.config.ts?");
    }
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
let jsMarkerSeenAnywhereInDist = false;
let cssMarkerSeenAnywhereInDist = false;

// Sanity check both marker choices against the WHOLE manifest (not just
// static closures) — MapLibre is still expected to ship somewhere
// (MapTab's lazy chunk, JS and CSS both), so if a marker is nowhere in
// dist/ at all, this check has silently stopped meaning anything for that
// half (wrong marker, a minifier/library change, or maplibre-gl no longer
// being used) and that's worth failing loudly on rather than a quiet,
// permanent pass.
for (const node of Object.values(manifest)) {
  if (node.file && !jsMarkerSeenAnywhereInDist) {
    const content = readTextOrNull(join(DIST_DIR, node.file));
    if (content && content.includes(JS_MARKER)) jsMarkerSeenAnywhereInDist = true;
  }
  if (!cssMarkerSeenAnywhereInDist) {
    for (const cssFile of node.css ?? []) {
      const content = readTextOrNull(join(DIST_DIR, cssFile));
      if (content && content.includes(CSS_MARKER)) {
        cssMarkerSeenAnywhereInDist = true;
        break;
      }
    }
  }
  if (jsMarkerSeenAnywhereInDist && cssMarkerSeenAnywhereInDist) break;
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
        // node.file is normally the JS chunk (CSS lives in node.css below),
        // but a manifest asset entry linked directly from index.html can
        // have a .css file here — match on the file's own type, not an
        // assumption about which field it came from.
        const marker = node.file.endsWith(".css") ? CSS_MARKER : JS_MARKER;
        if (content.includes(marker)) {
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
        `over the ${(STATIC_CLOSURE_BUDGET_BYTES / 1024).toFixed(0)} KiB budget. If this is an unrelated ` +
        "dependency growing (not MapLibre), either lazy-load it like MapTab or deliberately raise " +
        "STATIC_CLOSURE_BUDGET_BYTES in this file — don't ignore the failure.",
    );
    failed = true;
  }
}

// Manifest-graph blind spot guard: scan the BUILT dist/index.html's own
// <script>/<link> tags directly, independent of whether Vite's manifest
// tracked them. A hand-authored tag pointing at a file copied verbatim
// into dist/ (e.g. from public/) ships in the same initial HTML load as
// the entries checked above but has no manifest node, so the walk above
// can't see it. See the file-header comment for the full rationale.
{
  const indexHtmlPath = join(DIST_DIR, "index.html");
  const indexHtml = readTextOrNull(indexHtmlPath);
  if (indexHtml === null) {
    console.error(
      `check-entry-chunk: FAIL — could not read ${indexHtmlPath} to check for hand-authored <script>/<link> ` +
        "tags outside the Vite manifest's import graph.",
    );
    failed = true;
  } else {
    for (const url of collectHtmlAssetUrls(indexHtml)) {
      if (isExternalOrNonFileUrl(url)) continue;
      const assetPath = resolveHtmlAssetPath(url);
      const content = readTextOrNull(assetPath);
      // A missing local file here is a different failure mode (a broken
      // reference) than what this guard targets; leave it unflagged.
      if (content === null) continue;
      if (content.includes(JS_MARKER) || content.includes(CSS_MARKER)) {
        console.error(
          `check-entry-chunk: FAIL — index.html's <script>/<link> tag references "${url}", which contains ` +
            "MapLibre and is not part of the Vite manifest's static import graph (likely a hand-authored tag " +
            "pointing at a file copied verbatim into dist/, e.g. from public/). This bypasses the manifest " +
            "walk above; remove the tag and load MapLibre only via a dynamic import (React.lazy), like MapTab.",
        );
        failed = true;
      }
    }
  }
}

for (const [marker, seen] of [
  [JS_MARKER, jsMarkerSeenAnywhereInDist],
  [CSS_MARKER, cssMarkerSeenAnywhereInDist],
]) {
  if (seen) continue;
  console.error(
    `check-entry-chunk: FAIL — the "${marker}" marker was not found anywhere in ${DIST_DIR}. ` +
      "This check is meant to catch MapLibre in the entry chunk by finding a MapLibre-internal " +
      "marker string in the build output; if MapLibre is still a dependency, either the marker no " +
      "longer survives minification/CSS output or something else changed — investigate before " +
      "trusting this check's result. If maplibre-gl was intentionally removed, delete this check instead.",
  );
  failed = true;
}

if (failed) {
  process.exit(1);
}
console.log("check-entry-chunk: OK — MapLibre stays out of every entry's static closure, size within budget.");
