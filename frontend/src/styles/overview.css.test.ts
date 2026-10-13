import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ruleBody, decl } from "../test/cssRules";
import { contrastRatio } from "./tokens";

const css = readFileSync(resolve(__dirname, "./overview.css"), "utf-8");
const uiCss = readFileSync(resolve(__dirname, "../components/ui/ui.css"), "utf-8");

describe("overview.css page width", () => {
  it(".ov-page fills its parent, so a long headline cannot widen it past a phone screen", () => {
    const body = css.match(/\.ov-page\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(body).toMatch(/(^|;|\s)width:\s*100%/);
  });
});

describe("overview.css hover affordances change colour or opacity only", () => {
  it("the pareto track does not grow on hover", () => {
    expect(css).not.toMatch(/\.ov-pareto-row:hover \.ov-pareto-track\s*\{[^}]*height/);
    expect(decl(ruleBody(css, ".ov-pareto-fill"), "transition")).not.toMatch(/height/);
  });

  it("the pareto fill's rest and hover opacity both derive from its rank", () => {
    expect(decl(ruleBody(css, ".ov-pareto-fill"), "opacity")).toMatch(/var\(--rank-opacity, 1\)/);
    expect(decl(ruleBody(css, ".ov-pareto-row:hover .ov-pareto-fill"), "opacity")).toMatch(/var\(--rank-opacity, 1\)/);
  });

  it("a clickable card neither lifts nor changes elevation on hover", () => {
    const hover = css.match(/\.ov-card\.ov-clickable:hover\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(hover).not.toMatch(/transform|box-shadow/);
    expect(hover).toMatch(/background/);
    const uiHover = uiCss.match(/\.ui-card--clickable:hover\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(uiHover).not.toMatch(/transform|box-shadow/);
  });
});

describe("overview.css living hero", () => {
  it("the check bar slides by transform on --dur-3 and the value uses the numeric scale", () => {
    const fill = ruleBody(css, ".ov-check-fill {");
    expect(decl(fill, "transform")).toBe("translateX(calc((var(--bar-share, 0) - 1) * 100%))");
    expect(decl(ruleBody(css, ".ov-check-track {"), "overflow")).toBe("hidden");
    expect(decl(fill, "transition")).toBe("transform var(--dur-3) var(--ease-out), background-color var(--dur-3) var(--ease-out)");
    expect(decl(fill, "width")).toBe("100%");
    // A span: without a block box neither the width nor the transform applies.
    expect(decl(fill, "display")).toBe("block");
    expect(decl(ruleBody(css, ".ov-check-value {"), "font-size")).toBe("var(--text-sm)");
    expect(decl(ruleBody(css, ".ov-check-value {"), "font-family")).toBeNull();
  });
  it("a re-ranked row and a band header travel by transform on --dur-3", () => {
    expect(decl(ruleBody(css, ".ov-check-row {"), "transition")).toBe("transform var(--dur-3) var(--ease-out), background var(--transition)");
    expect(decl(ruleBody(css, ".ov-check-band-hd {"), "transition")).toBe("transform var(--dur-3) var(--ease-out)");
  });
  it("the breath loop lives inside the motion block, on --ease-in-out at 3 × --dur-4", () => {
    const motion = ruleBody(css, "@media (prefers-reduced-motion: no-preference)");
    expect(motion).toMatch(/\.ov-fresh-dot--live\s*\{[^}]*animation:\s*ov-breath calc\(var\(--dur-4\) \* 3\) var\(--ease-in-out\) infinite/);
    expect(css.replace(motion, "")).not.toMatch(/ov-breath/);
  });
  it("a stale feed's dot drops the ok green for a neutral tone", () => {
    expect(decl(ruleBody(css, ".ov-fresh-dot {"), "background")).toBe("var(--delay-text-ok)");
    expect(decl(ruleBody(css, ".ov-fresh-dot--stale {"), "background")).toBe("var(--text-tertiary)");
  });
  it("the ribbon's path transitions its geometry on --dur-3 only", () => {
    expect(decl(ruleBody(css, ".ov-pulse-ribbon path {"), "transition")).toBe("d var(--dur-3) var(--ease-out)");
  });
  it("the ribbon takes no pointer, and the figure column clips both charts under its text", () => {
    const ribbon = ruleBody(css, ".ov-pulse-ribbon {");
    expect(decl(ribbon, "pointer-events")).toBe("none");
    expect(decl(ribbon, "position")).toBe("absolute");
    expect(decl(ribbon, "z-index")).toBeNull();
    const figure = ruleBody(css, ".ov-hero-figure {");
    expect(decl(figure, "position")).toBe("relative");
    expect(decl(figure, "overflow")).toBe("hidden");
    // A non-positioned child would paint under the absolutely-filled charts.
    expect(decl(ruleBody(css, ".ov-hero-feed {"), "position")).toBe("relative");
  });
  it("the fact list sits on the card's plain surface, which does not clip the rows' focus ring", () => {
    const hero = ruleBody(css, ".ov-hero {");
    expect(decl(hero, "background")).toBe("var(--bg-surface)");
    expect(decl(hero, "overflow")).toBeNull();
    const text = ruleBody(css, ".ov-hero-text {");
    expect(decl(text, "background")).toBeNull();
    expect(decl(text, "overflow")).toBeNull();
  });
});

describe("overview.css hero facts", () => {
  const globalCss = readFileSync(resolve(__dirname, "./global.css"), "utf-8");
  const token = (block: string, prop: string) => decl(ruleBody(globalCss, block), prop)!;

  it("the verdict chip stays neutral, whatever the direction", () => {
    const chip = ruleBody(css, ".ov-hero-state {");
    expect(decl(chip, "color")).toBe("var(--text-secondary)");
    expect(decl(chip, "background")).toBe("var(--bg-soft)");
  });

  it("only the late-routes value takes the warm tone, and only as large text it can carry", () => {
    expect(decl(ruleBody(css, ".ov-hero-fact--attn .ov-hero-fact-value {"), "color")).toBe("var(--d4)");
    expect(decl(ruleBody(css, ".ov-hero-fact-value {"), "color")).toBe("var(--text-primary)");
    expect(css.match(/var\(--d4\)/g)).toHaveLength(1);
    // --text-xl is 26px, past WCAG's 24px large-text line, where 3:1 is AA.
    expect(decl(ruleBody(css, ".ov-hero-fact-value {"), "font-size")).toBe("var(--text-xl)");
    expect(token(":root {", "--text-xl")).toBe("26px");
    expect(contrastRatio(token(":root {", "--d4"), token(":root {", "--bg-surface"))).toBeGreaterThanOrEqual(4.5);
    expect(
      contrastRatio(token(':root[data-theme="dark"] {', "--d4"), token(':root[data-theme="dark"] {', "--bg-surface")),
    ).toBeGreaterThanOrEqual(3);
    // The unit beside the count keeps its own neutral tone.
    expect(decl(ruleBody(css, ".ov-hero-fact-unit {"), "color")).toBe("var(--text-tertiary)");
  });

  it("stacks the figure above the facts at the phone breakpoint", () => {
    const phone = css.match(/@media \(max-width: 640px\) \{\s*\.ov-hero \{([^}]*)\}/)?.[1] ?? "";
    expect(decl(phone, "grid-template-columns")).toBe("minmax(0, 1fr)");
  });
});

describe("overview.css peak label", () => {
  it("a tick is placed from its top, one line tall, so it hangs below the axis", () => {
    const tick = ruleBody(css, ".ov-peak-label--tick {");
    expect(decl(tick, "bottom")).toBeNull();
    expect(decl(tick, "line-height")).toBe("1");
  });
  it("a flipped peak label moves left and stays centred on its callout, like an unflipped one", () => {
    expect(decl(ruleBody(css, ".ov-peak-label--flip {"), "transform")).toBe("translate(-100%, -50%)");
  });
});
