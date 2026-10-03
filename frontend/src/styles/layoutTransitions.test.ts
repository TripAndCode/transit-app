import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Motion grammar: hover and state changes animate colour, opacity or
// transform. A transition on a layout property reflows every frame it runs,
// which is the one cost no token can make cheap. Checked over the source
// tree because the violations arrive one inline style at a time.

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const LAYOUT = /transition:\s*["'`]?[^;"'`}]*\b(width|height|top|left|right|bottom|padding|margin|grid-template-[a-z]+|all)\b/g;

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

describe("no transition animates layout", () => {
  it("names only colour, opacity, transform, filter or shadow in every transition under src", () => {
    const offenders: string[] = [];
    for (const file of walk(root)) {
      if (!/\.(css|tsx?)$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
      const source = readFileSync(file, "utf8");
      for (const m of source.matchAll(LAYOUT)) {
        offenders.push(`${path.relative(root, file)}: ${m[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
