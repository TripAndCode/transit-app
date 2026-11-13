// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./inspectCard.css"), "utf-8");

describe("inspectCard.css", () => {
  it("matches the dock/legend/fit family's glass and rounding through the same tokens, not a literal mix", () => {
    const card = ruleBody(css, ".ops-inspect {");
    expect(decl(card, "background")).toBe("var(--glass)");
    expect(decl(card, "border")).toContain("var(--glass-line)");
    expect(decl(card, "border-radius")).toBe("var(--radius-xl)");
  });
});
