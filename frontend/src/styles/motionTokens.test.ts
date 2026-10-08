// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Every duration goes through a --dur-* token (or --transition): the tokens
// are the one motion scale, and the reduced-motion zeroing of them is the
// only thing that reaches places the blanket `*` rule does not, such as
// view-transition pseudo-elements. `0s`/`0ms` and the `.01ms` reduced-motion
// idiom are not motion and stay allowed.
const root = path.resolve(process.cwd(), "src");

/** `file:selector list` literals that are deliberate, each with its reason;
 *  keyed on the whole list so another rule sharing a first selector is not
 *  exempted with it. */
const ALLOWED = new Set([
  // The short crossfade kept under reduced motion: the tokens are zeroed
  // there, and the blanket rule does not reach view-transition pseudos.
  "styles/viewTransitions.css:::view-transition-group(.page-nav), ::view-transition-old(.page-nav), ::view-transition-new(.page-nav)",
]);

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    return /\.(css|tsx?)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
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
      const sel = selector.trim().split(/\s*,\s*/).join(", ");
      for (const m of body.matchAll(/(?:^|[;\s])((?:transition|animation)(?:-[a-z-]+)?)\s*:\s*([^;]+)/g)) {
        if (LITERAL.test(m[2].replace(NOT_MOTION, "$1")) && !ALLOWED.has(`${rel}:${sel}`)) {
          out.push(`${rel}: ${sel} { ${m[1]}: ${m[2].trim().replace(/\s+/g, " ")} }`);
        }
      }
    }
  } else {
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
