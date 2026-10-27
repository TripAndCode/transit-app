import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

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
    ["../components/CopilotPanel.css", ".copilot-panel p"],
    ["../pages/ModelPage.css", ".model-page"],
    ["../pages/LoginPage.css", ".login-card__sub"],
    ["../pages/LoginPage.css", ".login-card__footer"],
  ])("%s %s breaks between phrases", (file, selector) => {
    expect(rule(file, selector)).toMatch(/word-break:\s*auto-phrase/);
  });
});
