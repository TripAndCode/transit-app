import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const css = readFileSync(resolve(__dirname, "./LandingPage.css"), "utf-8");

function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (!match) throw new Error(`selector not found: ${selector}`);
  return match[1];
}

describe("LandingPage.css hero sizing", () => {
  it(".landing-hero does not force a 100vh height", () => {
    expect(ruleBody(".landing-hero")).not.toMatch(/height:\s*100vh/);
  });

  it(".landing-hero keeps a min-height floor so the scene still has room on a short viewport", () => {
    expect(ruleBody(".landing-hero")).toMatch(/min-height:\s*\d/);
  });

  it(".landing-hero__content sizes from its own content, not a forced 100% height", () => {
    const body = ruleBody(".landing-hero__content");
    expect(body).not.toMatch(/height:\s*100%/);
    expect(body).not.toMatch(/justify-content:\s*center/);
  });
});
