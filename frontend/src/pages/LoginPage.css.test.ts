// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

// Every colour on the login card resolves through a theme token, so the dark
// theme's `:root[data-theme="dark"]` values reach it with no page-local pair.
const css = readFileSync(path.resolve(process.cwd(), "src/pages/LoginPage.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

describe("LoginPage.css", () => {
  it("declares no literal colour", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
  });

  it("paints the error from the calm error tokens, not an alarm red", () => {
    const body = ruleBody(css, ".login-card__error {");
    expect(decl(body, "background")).toBe("var(--error-bg)");
    expect(decl(body, "color")).toBe("var(--error-fg)");
  });

  it("turns the spinner on the ambient-loop token", () => {
    expect(css).toMatch(/\.login-card__spinner\s*\{\s*animation:\s*login-spin calc\(var\(--dur-4\) \* 0\.75\) linear infinite;/);
  });
});
