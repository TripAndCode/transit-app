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
  it("lets the freshness line wrap instead of overflowing the header", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const freshness = ruleBody(bpSm, ".ops-freshness {");
    expect(decl(freshness, "white-space")).toBe("normal");
    expect(decl(freshness, "min-width")).toBe("0");
    expect(decl(freshness, "overflow-wrap")).toBe("anywhere");
  });

  it("keeps a legend item whole instead of breaking it mid-word", () => {
    const chip = ruleBody(css, ".ops-map-ref__legend .legend-chip {");
    expect(decl(chip, "white-space")).toBe("nowrap");
  });
});
