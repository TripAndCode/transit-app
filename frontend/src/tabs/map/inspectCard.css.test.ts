// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./inspectCard.css"), "utf-8");
const mapCss = readFileSync(resolve(__dirname, "./operationsMap.css"), "utf-8");

describe("inspectCard.css", () => {
  it("matches the dock/legend/fit family's glass and rounding through the same tokens, not a literal mix", () => {
    const card = ruleBody(css, ".ops-inspect {");
    expect(decl(card, "background")).toBe("var(--glass)");
    expect(decl(card, "border")).toContain("var(--glass-line)");
    expect(decl(card, "border-radius")).toBe("var(--radius-xl)");
  });

  it("keeps its own backdrop-filter in step with the shared floating-chrome group's", () => {
    const card = ruleBody(css, ".ops-inspect {");
    const shared = ruleBody(mapCss, ".map-chrome,\n.ops-dock,\n.ops-map-ref,\n.ops-map-fit,\n.ops-playback {");
    const cardFilter = decl(card, "backdrop-filter");
    const sharedFilter = decl(shared, "backdrop-filter");
    expect(sharedFilter).not.toBeNull();
    expect(cardFilter).toBe(sharedFilter);
    const cardWebkitFilter = decl(card, "-webkit-backdrop-filter");
    const sharedWebkitFilter = decl(shared, "-webkit-backdrop-filter");
    expect(sharedWebkitFilter).not.toBeNull();
    expect(cardWebkitFilter).toBe(sharedWebkitFilter);
  });
});
