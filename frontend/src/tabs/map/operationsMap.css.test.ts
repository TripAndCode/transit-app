// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./operationsMap.css"), "utf-8");

describe("operationsMap.css floating chrome", () => {
  it("the playback rail joins the shared floating-chrome glass instead of carrying its own drifted copy", () => {
    const shared = ruleBody(css, ".map-chrome,\n.ops-dock,\n.ops-map-ref,\n.ops-map-fit,\n.ops-playback {");
    expect(decl(shared, "background")).toBe("var(--glass)");
    expect(decl(shared, "border")).toContain("var(--glass-line)");
  });

  it("the live breath lives only inside the no-preference motion block, on the token scale", () => {
    const motion = ruleBody(css, "@media (prefers-reduced-motion: no-preference)");
    expect(motion).toMatch(
      /\.ops-freshness--normal > span\s*\{[^}]*animation:\s*ops-freshness-breathe calc\(var\(--dur-4\) \* 3\) var\(--ease-in-out\) infinite/,
    );
    expect(css.replace(motion, "")).not.toMatch(/ops-freshness-breathe/);
  });

  it("the breath changes opacity only, never colour or scale", () => {
    const keyframes = ruleBody(css, "@keyframes ops-freshness-breathe");
    expect(keyframes).toMatch(/opacity/);
    expect(keyframes).not.toMatch(/background|transform|box-shadow/);
  });
});

describe("operationsMap.css phone layout (BP.sm)", () => {
  it("desktop keeps the freshness line on one line", () => {
    const freshness = ruleBody(css, ".ops-freshness {");
    expect(decl(freshness, "white-space")).toBe("nowrap");
  });

  it("gives the freshness line a half-width basis so it wraps instead of overflowing the header", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const freshness = ruleBody(bpSm, ".ops-freshness {");
    expect(decl(freshness, "flex")).toBe("1 1 50%");
    expect(decl(freshness, "min-width")).toBe("0");
    expect(decl(freshness, "white-space")).toBe("normal");
  });

  it("keeps the freshness dot a circle instead of shrinking it into an oval", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const dot = ruleBody(bpSm, ".ops-freshness > span {");
    expect(decl(dot, "flex")).toBe("none");
  });

  it("gives a refresh result its own row instead of squeezing the freshness line", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const result = ruleBody(bpSm, ".ops-refresh-result {");
    expect(decl(result, "flex")).toBe("1 1 100%");
  });

  it("desktop legend already wraps whole chips to a new row", () => {
    const legend = ruleBody(css, ".ops-map-ref__legend {");
    expect(decl(legend, "flex-wrap")).toBe("wrap");
  });

  it("keeps a legend item whole instead of breaking it mid-word, and leaves the legend's own wrap alone", () => {
    const phoneShell = ruleBody(css, "/* BP.sm phone shell");
    const chip = ruleBody(phoneShell, ".ops-map-ref__legend .legend-chip {");
    expect(decl(chip, "white-space")).toBe("nowrap");
    expect(phoneShell).not.toMatch(/\.ops-map-ref__legend\s*\{[^}]*flex-wrap:\s*nowrap/);
  });
});
