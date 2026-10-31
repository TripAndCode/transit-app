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
    expect(decl(ruleBody(css, ".ov-pareto-fill"), "transition") ?? "").not.toMatch(/height/);
  });

  it("the pareto fill is one neutral tone at every rank, as a share of delay is not a delay", () => {
    const fill = ruleBody(css, ".ov-pareto-fill {");
    expect(decl(fill, "background")).toBe("color-mix(in srgb, var(--trend-neutral) 50%, transparent)");
    expect(fill).not.toMatch(/--rank-opacity|--trend-bad/);
    expect(css).not.toMatch(/\.ov-pareto-row:hover \.ov-pareto-fill/);
    expect(decl(ruleBody(css, ".ov-pareto-row:hover {"), "background")).toBe("var(--hover-tint)");
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

  it("the verdict chip stays neutral, whatever the direction", () => {
    const chip = ruleBody(css, ".ov-hero-state {");
    expect(decl(chip, "color")).toBe("var(--text-secondary)");
    expect(decl(chip, "background")).toBe("var(--bg-soft)");
  });

  it("prints every fact value in ink, the late-routes count included", () => {
    expect(css).not.toMatch(/\.ov-hero-fact--attn/);
    expect(decl(ruleBody(css, ".ov-hero-fact-value {"), "color")).toBe("var(--text-primary)");
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

describe("overview.css route lists line their names up on one column", () => {
  const phone = ruleBody(css.slice(css.indexOf("/* ---------- Mobile ---------- */")), "@media (max-width: 640px) {");

  it.each([
    [".ov-pareto-list {", ".ov-pareto-row {", ".ov-pareto-label {"],
    [".ov-check-list {", ".ov-check-row {", ".ov-check-name {"],
  ])("%s gives the plate a track as wide as its widest plate, passed down to each label", (list, row, cell) => {
    const listBody = ruleBody(css, list);
    expect(decl(listBody, "display")).toBe("grid");
    expect(decl(listBody, "grid-template-columns")).toMatch(/^\[plate\] auto \[name\] minmax\(/);
    // The plate's own margin spaces it, so a list without plates starts its
    // names flush rather than after an empty gap.
    expect(decl(listBody, "column-gap")).toBe("0");
    const rowBody = ruleBody(css, row);
    expect(decl(rowBody, "grid-column")).toBe("1 / -1");
    expect(decl(rowBody, "grid-template-columns")).toBe("subgrid");
    const cellBody = ruleBody(css, cell);
    expect(decl(cellBody, "grid-column")).toBe("plate / bar");
    expect(decl(cellBody, "grid-template-columns")).toBe("subgrid");
  });

  it("on a phone, gives a concentration route's name the row's whole width, its bar and share beneath it", () => {
    expect(decl(ruleBody(phone, ".ov-pareto-list {"), "grid-template-columns")).toBe("[plate] auto [name] minmax(0, 1fr) [pct] auto");
    expect(decl(ruleBody(phone, ".ov-pareto-label {"), "grid-column")).toBe("plate / -1");
    const track = ruleBody(phone, ".ov-pareto-track {");
    expect(decl(track, "grid-column")).toBe("name / pct");
    expect(decl(track, "grid-row")).toBe("2");
    expect(decl(ruleBody(phone, ".ov-pareto-pct {"), "grid-row")).toBe("2");
  });
});

describe("overview.css route lists without subgrid", () => {
  // The build's browser floor (Vite's default target) predates subgrid in
  // Chromium, where the row would otherwise collapse to one stacked column.
  const fallback = ruleBody(css, "@supports not (grid-template-columns: subgrid) {");

  it("gives each row back its own columns, reading across as before", () => {
    expect(decl(ruleBody(fallback, ".ov-pareto-row {"), "grid-template-columns")).toBe("minmax(160px, 1.2fr) 2.5fr 56px");
    expect(decl(ruleBody(fallback, ".ov-check-row {"), "grid-template-columns")).toBe("minmax(0, 1fr) auto auto auto");
    // The list's named lines do not reach a row that is not a subgrid.
    for (const cell of [".ov-pareto-label {", ".ov-check-name {"]) {
      expect(decl(ruleBody(fallback, cell), "grid-column")).toBe("auto");
      expect(decl(ruleBody(fallback, cell), "display")).toBe("block");
    }
  });

  it("keeps a phone's name on its own line, the bar and share beneath it", () => {
    const phone = ruleBody(fallback, "@media (max-width: 640px) {");
    expect(decl(ruleBody(phone, ".ov-pareto-row {"), "grid-template-columns")).toBe("minmax(0, 1fr) auto");
    expect(decl(ruleBody(phone, ".ov-pareto-label {"), "grid-column")).toBe("1 / -1");
    expect(decl(ruleBody(phone, ".ov-pareto-track {"), "grid-column")).toBe("1");
    expect(decl(ruleBody(phone, ".ov-pareto-pct {"), "grid-column")).toBe("2");
  });

  it("comes after the phone rules it overrides", () => {
    expect(css.indexOf("@supports not (grid-template-columns: subgrid) {")).toBeGreaterThan(
      css.indexOf("/* ---------- Mobile ---------- */"),
    );
  });
});

