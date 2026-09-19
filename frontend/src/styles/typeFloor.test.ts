import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// CJK glyphs need more vertical room than Latin to stay legible: below 12px
// Japanese labels lose stroke separation entirely. 12px (`--text-xs`) is the
// floor for any text rendered as DOM content.
//
// Scope note: this checks the *declaration* forms that style DOM text --
// CSS `font-size:` and React style-object `fontSize:`. The JSX *attribute*
// form (`<text fontSize="10">`) is SVG-only -- chart tick labels, which are
// short numeric/latin axis marks drawn into a viewBox-scaled canvas and are
// deliberately exempt.
const MIN_PX = 12;

// `process.cwd()` is the `frontend/` package root under vitest; the module
// URL is not a file: URL after vite's transform, so it cannot be used here.
const root = path.resolve(process.cwd(), "src");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    if (/\.(css|tsx)$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) return [full];
    return [];
  });
}

const PATTERNS = [
  /font-size:\s*([\d.]+)px/g,
  /fontSize:\s*"?([\d.]+)(?:px)?"?\s*[,}]/g,
];

describe("CJK type floor", () => {
  it(`declares no DOM font size below ${MIN_PX}px`, () => {
    const offenders: string[] = [];
    for (const file of walk(root)) {
      const text = readFileSync(file, "utf8");
      const lines = text.split("\n");
      lines.forEach((line, i) => {
        for (const re of PATTERNS) {
          re.lastIndex = 0;
          for (const m of line.matchAll(re)) {
            if (parseFloat(m[1]) < MIN_PX) {
              offenders.push(`${path.relative(root, file)}:${i + 1}: ${m[0].trim()}`);
            }
          }
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
