// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./operationsMap.css"), "utf-8");

describe("operationsMap.css floating chrome", () => {
  it("the playback rail's glass tracks the theme's own --glass/--glass-line, not a fixed light-mode mix", () => {
    const playback = ruleBody(css, ".ops-playback {");
    expect(decl(playback, "background")).toBe("var(--glass)");
    expect(decl(playback, "border")).toContain("var(--glass-line)");
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
