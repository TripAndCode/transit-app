// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Every duration goes through a --dur-* token (or --transition), because the
// tokens are what prefers-reduced-motion zeroes: a literal `200ms` keeps
// moving for a viewer who asked for no motion. `0s`/`0ms` and the `.01ms`
// reduced-motion idiom are not motion and stay allowed.
const root = path.resolve(process.cwd(), "src");

/** `file:selector-or-line` entries owned by another change still in flight. */
const ALLOWED = new Set([
  // Its height transition goes away with the hover-height rule it serves.
  "styles/overview.css:.ov-pareto-fill",
  // The Spinner component moves its rotation onto a token in its own change.
  "components/Spinner.tsx",
]);

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    return /\.(css|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
  });
}

const LITERAL = /\b\d+(\.\d+)?m?s\b|(^|[\s,(])\.\d+m?s\b/;
const NOT_MOTION = /(^|[\s,(])(0m?s|\.01ms)\b/g;

function offendersIn(file: string): string[] {
  const rel = path.relative(root, file);
  const out: string[] = [];
  if (file.endsWith(".css")) {
    const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = selector.trim().split(/\s*,\s*/)[0];
      for (const m of body.matchAll(/(?:^|[;\s])((?:transition|animation)(?:-[a-z-]+)?)\s*:\s*([^;]+)/g)) {
        if (LITERAL.test(m[2].replace(NOT_MOTION, "$1")) && !ALLOWED.has(`${rel}:${sel}`)) {
          out.push(`${rel}: ${sel} { ${m[1]}: ${m[2].trim().replace(/\s+/g, " ")} }`);
        }
      }
    }
  } else {
    if (ALLOWED.has(rel)) return out;
    const tsx = readFileSync(file, "utf8");
    for (const m of tsx.matchAll(/\b((?:transition|animation)[A-Za-z]*)\s*:\s*(["'`])([^"'`]*)\2/g)) {
      if (LITERAL.test(m[3].replace(NOT_MOTION, "$1"))) out.push(`${rel}: ${m[1]}: ${m[3]}`);
    }
  }
  return out;
}

describe("motion durations come from tokens", () => {
  it("declares no literal transition or animation duration", () => {
    expect(walk(root).flatMap(offendersIn)).toEqual([]);
  });

  it("still catches a literal duration", () => {
    expect(LITERAL.test("opacity 200ms ease")).toBe(true);
    expect(LITERAL.test("spin .8s linear")).toBe(true);
    expect(LITERAL.test("opacity var(--dur-1) var(--ease-out)".replace(NOT_MOTION, "$1"))).toBe(false);
    expect(LITERAL.test("opacity 0s".replace(NOT_MOTION, "$1"))).toBe(false);
  });
});
