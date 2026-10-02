import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "./overview.css"), "utf-8");

describe("overview.css page width", () => {
  it(".ov-page fills its parent, so a long headline cannot widen it past a phone screen", () => {
    const body = css.match(/\.ov-page\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(body).toMatch(/(^|;|\s)width:\s*100%/);
  });
});
