import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// A stylesheet reaches the page only through a module's side-effect import, and
// nothing else notices when that import is missing: knip scans TS only, vitest
// stubs CSS, and the class names in the markup still read as styled. Every
// stylesheet under src must be imported by some non-test module.

// `process.cwd()` is the `frontend/` package root under vitest; the module
// URL is not a file: URL after vite's transform, so it cannot be used here.
const root = path.resolve(process.cwd(), "src");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

// Anchored at line start so a `//`-commented import never counts; block
// comments are stripped before matching for the same reason.
const CSS_IMPORT = /^import\s+["'](\.{1,2}\/[^"']+\.css)["'];?$/gm;
const BLOCK_COMMENT = /\/\*[\s\S]*?\*\//g;

describe("stylesheet imports", () => {
  it("imports every stylesheet under src from a non-test module", () => {
    const files = walk(root);
    const imported = new Set<string>();
    for (const file of files) {
      if (!/\.tsx?$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
      const source = readFileSync(file, "utf8").replace(BLOCK_COMMENT, "");
      for (const m of source.matchAll(CSS_IMPORT)) {
        imported.add(path.resolve(path.dirname(file), m[1]));
      }
    }
    const orphans = files
      .filter((f) => f.endsWith(".css") && !imported.has(f))
      .map((f) => path.relative(root, f));
    expect(orphans).toEqual([]);
  });
});
