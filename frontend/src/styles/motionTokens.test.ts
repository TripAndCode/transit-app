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

// The shared .lift/.glint/.live-dot primitives (global.css): each is read
// back out of the real file rather than re-declared here, so a future edit
// that breaks one of these invariants fails this test instead of silently
// drifting from it.
describe("shared motion primitives (.lift, .glint, .live-dot)", () => {
  const globalCss = readFileSync(path.join(root, "styles", "global.css"), "utf8");

  /** The declaration body of the first `{ ... }` block in file order whose
   *  selector list matches `selector` exactly, e.g. `.lift:hover,
   *  .lift:focus-visible`. The scan is a flat regex over the whole file,
   *  not aware of `@media` nesting, so a same-selector rule inside an
   *  earlier `@media` block is returned ahead of one outside it -- callers
   *  that need the unnested rule specifically pick a `selector` unique to
   *  it. Block comments are stripped first so a comment mentioning the
   *  selector can't be mistaken for one. */
  function ruleBody(css: string, selector: string): string | null {
    for (const [, sel, body] of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (sel.trim().split(/\s*,\s*/).join(", ") === selector) return body;
    }
    return null;
  }

  /** Every top-level `marker { ... }` block's span and body, matched by
   *  brace depth rather than a regex that stops at the block's first `}` --
   *  global.css holds several `@media (prefers-reduced-motion: ...)`
   *  blocks, each with nested rules of its own. */
  function blocksOf(css: string, marker: string): { start: number; end: number; body: string }[] {
    const out: { start: number; end: number; body: string }[] = [];
    let from = 0;
    for (let start; (start = css.indexOf(marker, from)) !== -1; ) {
      const openBrace = css.indexOf("{", start);
      let depth = 1;
      let i = openBrace + 1;
      while (depth > 0 && i < css.length) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") depth--;
        i++;
      }
      out.push({ start, end: i, body: css.slice(openBrace + 1, i - 1) });
      from = i;
    }
    return out;
  }

  /** Every `@media (prefers-reduced-motion: <mode>)` block's bodies,
   *  concatenated -- global.css declares more than one. */
  function reducedMotionBodies(css: string, mode: "reduce" | "no-preference"): string {
    return blocksOf(css, `@media (prefers-reduced-motion: ${mode})`)
      .map((b) => b.body)
      .join("\n");
  }

  /** `css` with every `@media (prefers-reduced-motion: <mode>)` block
   *  removed entirely, so a rule that survives is one reduced motion can
   *  still reach. */
  function withoutReducedMotionBlocks(css: string, mode: "reduce" | "no-preference"): string {
    const blocks = blocksOf(css, `@media (prefers-reduced-motion: ${mode})`);
    let out = "";
    let last = 0;
    for (const b of blocks) {
      out += css.slice(last, b.start);
      last = b.end;
    }
    return out + css.slice(last);
  }

  it("times .lift and .glint only through var(--dur-*)/var(--ease-*)", () => {
    const lift = ruleBody(globalCss, ".lift");
    expect(lift).toMatch(/transition:\s*var\(--lift-transition\)/);
    // --lift-transition itself (the token .lift and every host composing
    // alongside it both resolve to) carries the real timing.
    const liftTransition = globalCss.match(/--lift-transition:\s*([^;]+);/)?.[1] ?? "";
    expect(liftTransition).toMatch(/var\(--dur-\d\)\s+var\(--ease-[a-z-]+\)/);
    const noPref = reducedMotionBodies(globalCss, "no-preference");
    expect(noPref).toMatch(/\.glint\s*\{\s*animation:\s*glint-sweep\s+var\(--dur-\d\)\s+var\(--ease-[a-z-]+\)/);
  });

  // Shared by the real assertion and its negative-control self-test below,
  // so the two can never drift apart into checking different things.
  const LIVE_RING_TIMING_RE =
    /animation:\s*live-ring\s+calc\(var\(--dur-\d\)[^)]*\)\s+var\(--ease-[a-z-]+\)\s+infinite/;

  it("times .live-dot's loop only through var(--dur-*)/var(--ease-*)", () => {
    expect(globalCss).toMatch(new RegExp(`\\.live-dot\\s*\\{\\s*${LIVE_RING_TIMING_RE.source}`));
  });

  it("fails this test's own matcher on a hard-coded duration, proving it would catch one", () => {
    expect("animation: live-ring 2.4s ease-in-out infinite").not.toMatch(LIVE_RING_TIMING_RE);
  });

  it("gives .lift's hover/focus lift no transform under reduced motion", () => {
    const reduced = reducedMotionBodies(globalCss, "reduce");
    const lift = ruleBody(reduced, ".lift:hover, .lift:focus-visible");
    expect(lift).not.toBeNull();
    expect(lift).toMatch(/transform:\s*none/);
  });

  it("turns .glint fully off (invisible, not just instant) under reduced motion", () => {
    // The base rule (outside every @media) is the reduced-motion resting
    // state: opacity 0, so there is nothing to see without the
    // no-preference block's animation ever running.
    const base = ruleBody(globalCss, ".glint");
    expect(base).toMatch(/opacity:\s*0\b/);
    // And that animation lives only inside the no-preference block(s) --
    // not declared again anywhere reduced motion can still reach it.
    const outsideNoPreference = withoutReducedMotionBlocks(globalCss, "no-preference");
    expect(outsideNoPreference).not.toMatch(/\.glint\s*\{[^}]*animation:/);
  });

  it("explicitly turns .live-dot's loop off under reduced motion", () => {
    const reduced = reducedMotionBodies(globalCss, "reduce");
    expect(ruleBody(reduced, ".live-dot")).toMatch(/animation:\s*none/);
  });

  it("limits .lift's hover transform to at most 4px", () => {
    const lift = ruleBody(globalCss, ".lift:hover, .lift:focus-visible");
    const translate = lift?.match(/translateY\((-?\d+(?:\.\d+)?)px\)/);
    expect(translate).not.toBeNull();
    expect(Math.abs(Number(translate![1]))).toBeLessThanOrEqual(4);
  });
});
