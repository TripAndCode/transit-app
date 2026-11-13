// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ruleBody, decl, withoutBlocks } from "../test/cssRules";

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

// The shared .lift/.glint/.live-dot primitives (global.css): each is read
// back out of the real file rather than re-declared here, so a future edit
// that breaks one of these invariants fails this test instead of silently
// drifting from it.
describe("shared motion primitives (.lift, .glint, .live-dot)", () => {
  const globalCss = readFileSync(path.join(root, "styles", "global.css"), "utf8").replace(
    /\/\*[\s\S]*?\*\//g,
    "",
  );
  const rootBlock = ruleBody(globalCss, ":root {");

  /** Every `@media (prefers-reduced-motion: <mode>)` block whose body
   *  contains `selector` -- global.css declares more than one block per
   *  mode, each guarding a different rule. Mirrors the
   *  matchAll()-then-ruleBody() idiom tokens.test.ts uses for the
   *  no-preference case, parameterized over both modes. */
  function reducedMotionBlocksContaining(mode: "reduce" | "no-preference", selector: string): string[] {
    const marker = `@media (prefers-reduced-motion: ${mode})`;
    const markerRe = new RegExp(`@media \\(prefers-reduced-motion: ${mode}\\)`, "g");
    return [...globalCss.matchAll(markerRe)]
      .map((m) => ruleBody(globalCss.slice(m.index), marker))
      .filter((block) => block.includes(selector));
  }

  it("times .lift and .glint only through var(--dur-*)/var(--ease-*)", () => {
    const lift = ruleBody(globalCss, ".lift {");
    expect(decl(lift, "transition")).toBe("var(--lift-transition)");
    // --lift-transition itself (the token .lift and every host composing
    // alongside it both resolve to) carries the real timing.
    expect(decl(rootBlock, "--lift-transition")).toMatch(/var\(--dur-\d\)\s+var\(--ease-[a-z-]+\)/);
    const [noPref] = reducedMotionBlocksContaining("no-preference", ".glint {");
    expect(decl(ruleBody(noPref, ".glint {"), "animation")).toMatch(/glint-sweep\s+var\(--dur-\d\)\s+var\(--ease-[a-z-]+\)/);
  });

  // Shared by the real assertion and its negative-control self-test below,
  // so the two can never drift apart into checking different things.
  const LIVE_RING_TIMING_RE = /^live-ring\s+calc\(var\(--dur-\d\)[^)]*\)\s+var\(--ease-[a-z-]+\)\s+infinite$/;

  it("times .live-dot's loop only through var(--dur-*)/var(--ease-*)", () => {
    expect(decl(ruleBody(globalCss, ".live-dot {"), "animation")).toMatch(LIVE_RING_TIMING_RE);
  });

  it("fails this test's own matcher on a hard-coded duration, proving it would catch one", () => {
    expect("live-ring 2.4s ease-in-out infinite").not.toMatch(LIVE_RING_TIMING_RE);
  });

  it("gives .lift's hover/focus lift no transform under reduced motion", () => {
    const [reduced] = reducedMotionBlocksContaining("reduce", ".lift:hover, .lift:focus-visible {");
    expect(decl(ruleBody(reduced, ".lift:hover, .lift:focus-visible {"), "transform")).toBe("none");
  });

  it("turns .glint fully off (invisible, not just instant) under reduced motion", () => {
    // The base rule (outside every @media) is the reduced-motion resting
    // state: opacity 0, so there is nothing to see without the
    // no-preference block's animation ever running.
    expect(decl(ruleBody(globalCss, ".glint {"), "opacity")).toBe("0");
    // And that animation lives only inside the no-preference block(s) --
    // not declared again anywhere reduced motion can still reach it.
    const outsideNoPreference = withoutBlocks(globalCss, "@media (prefers-reduced-motion: no-preference)");
    expect(outsideNoPreference).not.toMatch(/\.glint\s*\{[^}]*animation:/);
  });

  it("explicitly turns .live-dot's loop off under reduced motion", () => {
    const [reduced] = reducedMotionBlocksContaining("reduce", ".live-dot {");
    expect(decl(ruleBody(reduced, ".live-dot {"), "animation")).toBe("none");
  });

  it("limits .lift's hover transform to at most 4px", () => {
    const transform = decl(ruleBody(globalCss, ".lift:hover, .lift:focus-visible {"), "transform");
    const translate = transform?.match(/translateY\((-?\d+(?:\.\d+)?)px\)/);
    expect(translate).not.toBeNull();
    expect(Math.abs(Number(translate![1]))).toBeLessThanOrEqual(4);
  });
});
