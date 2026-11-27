import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

// Japanese has no spaces to break at, so a headline or a short paragraph
// wraps mid-word ("ほぼ同じ / 水準") unless it asks to break between phrases.
function rule(file: string, selector: string): string {
  const css = readFileSync(resolve(__dirname, file), "utf-8");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`(^|\\n)${escaped}\\s*\\{([^}]*)\\}`))?.[2] ?? "";
}

describe("Japanese line breaking", () => {
  it.each([
    ["global.css", "h1, h2, h3"],
    ["overview.css", ".ov-hero-story"],
    ["../pages/ModelPage.css", ".model-page"],
    ["../pages/LoginPage.css", ".login-card__sub"],
    ["../pages/LoginPage.css", ".login-card__footer"],
  ])("%s %s breaks between phrases", (file, selector) => {
    expect(rule(file, selector)).toMatch(/word-break:\s*auto-phrase/);
  });
});

describe("phone-width wrapping", () => {
  it("lets a long reference chip wrap instead of widening its figure past a phone", () => {
    const css = readFileSync(resolve(__dirname, "global.css"), "utf-8");
    const chip = ruleBody(ruleBody(css, "@media (max-width: 640px) {"), ".reference-chip {");
    expect(decl(chip, "white-space")).toBe("normal");
    expect(decl(chip, "max-width")).toBe("100%");
  });
});
