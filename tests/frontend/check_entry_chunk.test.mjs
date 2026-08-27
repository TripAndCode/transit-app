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

function makeDist(files) {
  const dir = mkdtempSync(join(tmpdir(), "check-entry-chunk-"));
  tmpDirs.push(dir);
  for (const [relPath, content] of Object.entries(files)) {
    const full = join(dir, relPath);
    mkdirSync(join(full, ".."), { recursive: true });
    writeFileSync(full, content);
  }
  return dir;
}

function run(distDir) {
  return spawnSync("node", [SCRIPT_PATH], {
    env: { ...process.env, CHECK_ENTRY_CHUNK_DIST_DIR: distDir },
    encoding: "utf8",
  });
}

// Benign entry chunk that still contains the JS_MARKER — required so the
// "marker seen anywhere in dist/" sanity check passes in scenarios where
// the violation being tested is elsewhere (e.g. the size budget, or a
// separate statically-imported chunk).
const BENIGN_LAZY_CHUNK = `// pretend MapTab lazy chunk\nexport const status = () => "${JS_MARKER}";\n`;

test("clean build: no marker in the static closure, under budget -> exit 0", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
      "src/MapTab.tsx": { file: "assets/MapTab.js", dynamicImports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    "assets/MapTab.js": BENIGN_LAZY_CHUNK,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("MapLibre statically imported (JS marker in a static chunk) -> exit 1", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: ["src/vendor.ts"], dynamicImports: [] },
      "src/vendor.ts": { file: "assets/vendor.js", imports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    "assets/vendor.js": `${JS_MARKER}();`,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /contains MapLibre/);
});

test("MapLibre only in a dynamic (lazy) chunk -> exit 0 (lazy loading is allowed)", () => {
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: [], dynamicImports: ["src/MapTab.tsx"] },
      "src/MapTab.tsx": { file: "assets/MapTab.js", imports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    "assets/MapTab.js": `${JS_MARKER}();`,
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
      "src/MapTab.tsx": { file: "assets/MapTab.js", imports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    "assets/index.css": `.${CSS_MARKER} { position: absolute; }`,
    "assets/MapTab.js": BENIGN_LAZY_CHUNK,
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
      "src/MapTab.tsx": { file: "assets/MapTab.js", imports: [] },
    }),
    "assets/index.js": "console.log('hello');",
    // Real-world shape: app code themes a handful of MapLibre-generated
    // classes without statically bundling the library itself.
    "assets/index.css": ".maplibregl-popup-content { color: red; } .maplibregl-popup-tip { display: none; }",
    "assets/MapTab.js": BENIGN_LAZY_CHUNK,
  });
  const result = run(dist);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("vendor split defeats a naive entry-only budget, but the summed static closure still fails", () => {
  const bigChunk = "x".repeat(700 * 1024); // 700 KiB, over the 600 KiB budget on its own
  const dist = makeDist({
    ".vite/manifest.json": JSON.stringify({
      "index.html": { file: "assets/index.js", isEntry: true, imports: ["src/vendor.ts"], dynamicImports: ["src/MapTab.tsx"] },
      "src/vendor.ts": { file: "assets/vendor.js", imports: [] },
      "src/MapTab.tsx": { file: "assets/MapTab.js", imports: [] },
    }),
    "assets/index.js": "console.log('small entry file');", // tiny on its own
    "assets/vendor.js": bigChunk, // statically imported, pushes the closure over budget
    "assets/MapTab.js": BENIGN_LAZY_CHUNK,
  });
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /over the 600 KiB budget/);
});

test("marker not found anywhere in dist/ -> exit 1 (the check itself may be broken)", () => {
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

test("missing manifest -> exit 1 with an actionable message", () => {
  const dist = makeDist({});
  const result = run(dist);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /build\.manifest: true/);
});
