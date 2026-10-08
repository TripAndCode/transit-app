// @vitest-environment node
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    return /\.(ts|tsx|css)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
  });
}
const files = walk(root).map((f) => ({ file: path.relative(root, f), text: readFileSync(f, "utf8") }));

describe("motion grammar", () => {
  it("no component asks a count-up to climb from 0 on mount", () => {
    const offenders = files.filter((f) => /entrance\s*:\s*true/.test(f.text)).map((f) => f.file);
    expect(offenders).toEqual([]);
  });
  it("no component gates an entrance on mount instead of first data", () => {
    const offenders = files.filter((f) => /useEnteredOnMount/.test(f.text)).map((f) => f.file);
    expect(offenders).toEqual([]);
  });
  it("the staggered grid fade uses --dur-2 so the stagger total stays inside --dur-3", () => {
    const css = files.find((f) => f.file === "styles/global.css")!.text;
    expect(css).toMatch(/\.chart-cell-enter\s*\{[^}]*transition:\s*opacity var\(--dur-2\) var\(--ease-out\)/);
  });
});
