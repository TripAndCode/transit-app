// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./focusedOverview.css"), "utf-8");
const pageCss = readFileSync(resolve(__dirname, "./operationsMap.css"), "utf-8");

describe("focusedOverview.css phone layout (BP.sm)", () => {
  it("shrinks the title off the desktop token and tightens the header's own line-height", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const title = ruleBody(bpSm, ".focused-overview .ops-heading h1 {");
    expect(decl(title, "font")).toBe("650 var(--text-lg) / 1.2 var(--font-body, sans-serif)");
  });

  it("tightens the column gap above the map below the desktop value", () => {
    const bpSm = ruleBody(css, "@media (max-width: 640px) {");
    const page = ruleBody(bpSm, ".focused-overview.operations-page {");
    // The page's desktop gap is the shared `.operations-page` rule's.
    const desktopGap = decl(ruleBody(pageCss, ".operations-page {"), "gap");
    expect(decl(page, "gap")).toBe("8px");
    expect(desktopGap).toBe("10px");
  });
});
