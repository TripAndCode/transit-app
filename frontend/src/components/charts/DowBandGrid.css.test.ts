// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";

const css = readFileSync(resolve(process.cwd(), "src/components/charts/DowBandGrid.css"), "utf8");

describe("DowBandGrid.css", () => {
  it("never breaks a band's name or hours across lines", () => {
    expect(decl(ruleBody(css, ".band-grid__name {"), "white-space")).toBe("nowrap");
    expect(decl(ruleBody(css, ".band-grid__hours {"), "white-space")).toBe("nowrap");
  });

  it("drops the name, not the hours, on a phone, where a column is narrower than a name", () => {
    const phone = ruleBody(css, "@media (max-width: 640px) {");
    expect(decl(ruleBody(phone, ".band-grid__name {"), "display")).toBe("none");
    expect(phone).not.toMatch(/band-grid__hours/);
  });
});
