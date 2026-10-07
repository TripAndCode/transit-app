import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ruleBody, decl } from "../test/cssRules";

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
  it("the ribbon takes no pointer and both hero columns paint over it", () => {
    const ribbon = ruleBody(css, ".ov-pulse-ribbon {");
    expect(decl(ribbon, "pointer-events")).toBe("none");
    expect(decl(ribbon, "position")).toBe("absolute");
    expect(decl(ribbon, "z-index")).toBeNull();
    expect(decl(ruleBody(css, ".ov-hero {"), "position")).toBe("relative");
    expect(decl(ruleBody(css, ".ov-hero-figure {"), "position")).toBe("relative");
    expect(decl(ruleBody(css, ".ov-hero-text {"), "position")).toBe("relative");
  });
});
