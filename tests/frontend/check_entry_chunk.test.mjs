import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { after, test } from "node:test";

const SCRIPT_PATH = fileURLToPath(new URL("../../frontend/scripts/check-entry-chunk.mjs", import.meta.url));
const JS_MARKER = "getRTLTextPluginStatus";
const CSS_MARKER = "maplibregl-canvas";

const tmpDirs = [];
after(() => {
  for (const dir of tmpDirs) rmSync(dir, { recursive: true, force: true });
});

// Real Vite builds always emit dist/index.html alongside .vite/manifest.json
// (see check-entry-chunk.mjs's "manifest-graph blind spot" scan, which reads
// it directly). Auto-inject a benign default here so the ~10 existing tests
// that only care about the manifest-graph behavior don't all need to supply
// one by hand; tests exercising the index.html scan itself pass an explicit
// override via the `indexHtml` option.
const DEFAULT_INDEX_HTML = `<!doctype html>
<html>
  <head><link rel="icon" href="/favicon.svg" /></head>
  <body><script type="module" src="/assets/index.js"></script></body>
</html>`;

function makeDist(files, { indexHtml = DEFAULT_INDEX_HTML } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "check-entry-chunk-"));
  tmpDirs.push(dir);
  for (const [relPath, content] of Object.entries(files)) {
    const full = join(dir, relPath);
    mkdirSync(join(full, ".."), { recursive: true });
    writeFileSync(full, content);
  }
  if (indexHtml !== null) {
    writeFileSync(join(dir, "index.html"), indexHtml);
  }
  return dir;
}

function run(distDir) {
  return spawnSync("node", [SCRIPT_PATH, "--dist-dir", distDir], {
    encoding: "utf8",
  });
}

// Benign lazy MapTab chunk (JS + CSS) that contains both markers —
// required so the "marker seen anywhere in dist/" liveness checks pass in
// scenarios where the violation being tested is elsewhere (e.g. the size
// budget, or a separate statically-imported chunk). Spread MAPTAB_MANIFEST_NODE
// into a manifest's "src/MapTab.tsx" entry and MAPTAB_FILES into its files.
const BENIGN_LAZY_CHUNK = `// pretend MapTab lazy chunk\nexport const status = () => "${JS_MARKER}";\n`;
const BENIGN_LAZY_CSS = `.${CSS_MARKER} { position: absolute; }`;
const MAPTAB_MANIFEST_NODE = { file: "assets/MapTab.js", css: ["assets/MapTab.css"], imports: [] };
const MAPTAB_FILES = { "assets/MapTab.js": BENIGN_LAZY_CHUNK, "assets/MapTab.css": BENIGN_LAZY_CSS };

test("clean build: no marker in the static closure, under budget -> exit 0", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('hello');",
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("MapLibre statically imported (JS marker in a static chunk) -> exit 1", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: ["src/vendor.ts"], dynamicImports: ["src/MapTab.tsx"] },
      "src/vendor.ts": { file: "assets/vendor.js", imports: [] },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('hello');",
    "assets/vendor.js": `${JS_MARKER}();`,
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /contains MapLibre/);
});

test("MapLibre only in a dynamic (lazy) chunk -> exit 0 (lazy loading is allowed)", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('hello');",
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("MapLibre CSS statically reachable via entry.css -> exit 1", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": {
        file: "assets/index.js",
        isEntry: true,
        imports: [],
        css: ["assets/index.css"],
        dynamicImports: ["src/MapTab.tsx"],
      },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('hello');",
    "assets/index.css": BENIGN_LAZY_CSS,
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /contains MapLibre's stylesheet/);
});

test("app-authored MapLibre class overrides in entry CSS do not false-positive (regression: global.css themes .maplibregl-popup-content)", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": {
        file: "assets/index.js",
        isEntry: true,
        imports: [],
        css: ["assets/index.css"],
        dynamicImports: ["src/MapTab.tsx"],
      },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('hello');",
    // Real-world shape: app code themes a handful of MapLibre-generated
    // classes without statically bundling the library itself.
    "assets/index.css": ".maplibregl-popup-content { color: red; } .maplibregl-popup-tip { display: none; }",
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("vendor split defeats a naive entry-only budget, but the summed static closure still fails", () => {
  // Generously over the script's STATIC_CLOSURE_BUDGET_BYTES (600 KiB as of
  // writing) -- not derived from it (the script has no exports), so if that
  // constant is ever raised well past 4 MiB this fixture needs bumping too.
  const bigChunk = "x".repeat(4 * 1024 * 1024);
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: ["src/vendor.ts"], dynamicImports: ["src/MapTab.tsx"] },
      "src/vendor.ts": { file: "assets/vendor.js", imports: [] },
      "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
    }),
    "assets/index.js": "console.log('small entry file');", // tiny on its own
    "assets/vendor.js": bigChunk, // statically imported, pushes the closure over budget
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /over the \d+ KiB budget/);
});

test("neither marker found anywhere in dist/ -> exit 1 (the check itself may be broken)", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: [] },
    }),
    "assets/index.js": "console.log('no maplibre anywhere');",
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /marker was not found anywhere/);
});

test("JS marker present but CSS marker absent from dist/ -> exit 1 (CSS half of the check may be broken)", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
      "src/MapTab.tsx": { file: "assets/MapTab.js", imports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    "assets/MapTab.js": BENIGN_LAZY_CHUNK,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, new RegExp(`"${CSS_MARKER}" marker was not found anywhere`));
});

test("missing manifest -> exit 1 with an actionable message", () => {
  const dist = makeDist({});
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /build\.manifest: true/);
});

// Regression coverage for the manifest-graph blind spot: a hand-authored
// <script>/<link> tag in index.html pointing at a file copied verbatim into
// dist/ (e.g. from public/) has no manifest node at all, so the static
// import-graph walk above can't see it even though it ships in the same
// initial HTML load. These fixtures otherwise pass a clean manifest (no
// MapLibre statically imported, under budget) to prove the index.html scan
// is what's catching the violation, not the manifest walk.
const CLEAN_MANIFEST = {
  "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
  "src/MapTab.tsx": MAPTAB_MANIFEST_NODE,
};

test("hand-authored <script src> in index.html bypasses the manifest but is still caught", () => {
  const dist = makeDist(
    {
      ".vite/manifest.json": JSON.stringify(CLEAN_MANIFEST),
      "assets/index.js": "console.log('hello');",
      // Not referenced by any manifest node — simulates a file copied
      // verbatim from public/ and wired up by a hand-authored tag.
      "vendor-maplibre.js": `${JS_MARKER}();`,
      ...MAPTAB_FILES,
    },
    {
      indexHtml: `<!doctype html>
<html>
  <body>
    <script type="module" src="/assets/index.js"></script>
    <script src="/vendor-maplibre.js"></script>
  </body>
</html>`,
    },
  );
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /not part of the Vite manifest's static import graph/);
});

test("hand-authored <link href> in index.html bypasses the manifest but is still caught", () => {
  const dist = makeDist(
    {
      ".vite/manifest.json": JSON.stringify(CLEAN_MANIFEST),
      "assets/index.js": "console.log('hello');",
      "vendor-maplibre.css": BENIGN_LAZY_CSS,
      ...MAPTAB_FILES,
    },
    {
      indexHtml: `<!doctype html>
<html>
  <head><link rel="stylesheet" href="/vendor-maplibre.css" /></head>
  <body><script type="module" src="/assets/index.js"></script></body>
</html>`,
    },
  );
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /not part of the Vite manifest's static import graph/);
});

test("external <link href> (e.g. Google Fonts) is not resolved against dist/ and does not false-positive", () => {
  const dist = makeDist(
    {
      ".vite/manifest.json": JSON.stringify(CLEAN_MANIFEST),
      "assets/index.js": "console.log('hello');",
      ...MAPTAB_FILES,
    },
    {
      indexHtml: `<!doctype html>
<html>
  <head>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP" rel="stylesheet">
  </head>
  <body><script type="module" src="/assets/index.js"></script></body>
</html>`,
    },
  );
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("clean index.html with only Vite-managed tags -> exit 0", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify(CLEAN_MANIFEST),
    "assets/index.js": "console.log('hello');",
    ...MAPTAB_FILES,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("missing dist/index.html -> exit 1 with an actionable message", () => {
  const dist = makeDist(
    {
      ".vite/manifest.json": JSON.stringify(CLEAN_MANIFEST),
      "assets/index.js": "console.log('hello');",
      ...MAPTAB_FILES,
    },
    { indexHtml: null },
  );
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /could not read/);
});
