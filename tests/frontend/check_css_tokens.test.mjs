import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { after, test } from "node:test";
import { DYNAMIC_PER_INSTANCE_PROPERTIES } from "../../frontend/scripts/dynamicCssProperties.mjs";

const SCRIPT_PATH = fileURLToPath(new URL("../../frontend/scripts/check-css-tokens.mjs", import.meta.url));

const tmpDirs = [];
after(() => {
  for (const dir of tmpDirs) rmSync(dir, { recursive: true, force: true });
});

const DEFAULT_GLOBAL_CSS = `:root {
  --bg-surface: #ffffff;
  --text-secondary: #6a6a6a;
  --z-dropdown: 20;
  --z-popover: 30;
}
`;

function makeSrcTree(files, { globalCss = DEFAULT_GLOBAL_CSS } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "check-css-tokens-"));
  tmpDirs.push(dir);
  mkdirSync(join(dir, "styles"), { recursive: true });
  writeFileSync(join(dir, "styles", "global.css"), globalCss);
  for (const [relPath, content] of Object.entries(files)) {
    const full = join(dir, relPath);
    mkdirSync(join(full, ".."), { recursive: true });
    writeFileSync(full, content);
  }
  return dir;
}

function run(srcDir) {
  return spawnSync("node", [SCRIPT_PATH, "--src-dir", srcDir], { encoding: "utf8" });
}

test("clean tree: every var(--x) resolves, every z-index uses the ladder -> exit 0", () => {
  const src = makeSrcTree({
    "components/Widget.css": ".widget { background: var(--bg-surface); z-index: var(--z-dropdown); }",
    "components/Widget.tsx": `export const s = { color: "var(--text-secondary)" };`,
  });
  const result = run(src);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("var(--x) with no fallback and no global.css definition -> exit 1", () => {
  const src = makeSrcTree({
    "components/Widget.css": ".widget { border-radius: var(--radius-md); }",
  });
  const result = run(src);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /--radius-md/);
  assert.match(result.stderr, /not defined anywhere/);
});

test("var(--x, fallback) is exempt from the global.css definition requirement", () => {
  const src = makeSrcTree({
    "components/Widget.tsx": `export const s = { width: "var(--ops-queue-width, 380px)" };`,
  });
  const result = run(src);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("fallback containing nested parens (e.g. rgba(...)) is parsed correctly, not truncated", () => {
  // --accent-soft is declared (unlike the undefined-token cases below), so
  // this exercises only the paren-depth parsing: a naive scan that stops at
  // the first `)` would truncate the fallback inside `rgba(...)` and
  // misidentify the outer var()'s closing paren.
  const src = makeSrcTree(
    {
      "components/Widget.css": ".widget { background: var(--accent-soft, rgba(91, 108, 173, 0.14)); }",
    },
    { globalCss: `:root { --accent-soft: #e1f1f1; }` },
  );
  const result = run(src);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("var(--x, <literal>) naming an undefined, non-dynamic token -> exit 1", () => {
  // The actual bug this guards against: --radius-md was never declared
  // anywhere in global.css (only --radius/--radius-lg/--radius-xl were), so
  // its literal fallback silently masked the missing token instead of
  // catching it.
  const src = makeSrcTree({
    "components/Widget.tsx": `export const s = { borderRadius: "var(--radius-md, 10px)" };`,
  });
  const result = run(src);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /--radius-md/);
  assert.match(result.stderr, /literal value, not another token/);
});

test("var(--x, var(--y)) stays exempt even when --x is undefined", () => {
  const src = makeSrcTree({
    "components/Widget.css": ".widget { background: var(--map-badge-bg, var(--bg-surface)); }",
  });
  const result = run(src);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("a known dynamic per-instance property with a literal fallback stays exempt", () => {
  const src = makeSrcTree({
    "tabs/MapTab.css": ".ops-workspace { grid-template-columns: var(--ops-queue-width, 380px); }",
  });
  const result = run(src);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("hardcoded z-index literal in CSS -> exit 1", () => {
  const src = makeSrcTree({
    "components/Widget.css": ".widget { position: absolute; z-index: 50; }",
  });
  const result = run(src);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /z-index: 50/);
  assert.match(result.stderr, /shared/);
});

test("z-index referencing a non-z var (var(--foo)) still fails the ladder check", () => {
  const src = makeSrcTree({
    "components/Widget.css": ".widget { z-index: var(--foo); }",
  }, { globalCss: `:root { --foo: 5; }` });
  const result = run(src);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /var\(--foo\)/);
});

test("missing global.css -> exit 1 with an actionable message", () => {
  const dir = mkdtempSync(join(tmpdir(), "check-css-tokens-"));
  tmpDirs.push(dir);
  const result = run(dir);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /could not read/);
});

test("the dynamic-property allowlist is exactly the three runtime-set properties", () => {
  // This set is the one way a `var(--x, <literal>)` naming an undefined
  // token can pass, so widening it has to be a deliberate edit here rather
  // than a line nobody notices in a diff.
  assert.deepEqual([...DYNAMIC_PER_INSTANCE_PROPERTIES].sort(), ["--cell-opacity", "--len", "--ops-queue-width"]);
});
