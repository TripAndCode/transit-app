import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import { Z_INDEX } from "./zIndex";

const GLOBAL_CSS_PATH = join(dirname(fileURLToPath(import.meta.url)), "global.css");
const globalCss = readFileSync(GLOBAL_CSS_PATH, "utf8");

// Mirrors the source of truth (Z_INDEX) into a `--z-<name>` custom property
// per key, so CSS files that can't import the TS constant still stack
// against the same ladder rather than picking their own numbers. This test
// is the only thing keeping the two declarations from drifting apart.
describe("Z_INDEX / global.css mirror", () => {
  for (const [name, value] of Object.entries(Z_INDEX)) {
    const cssVarName = `--z-${name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;

    test(`${cssVarName} mirrors Z_INDEX.${name} (${value})`, () => {
      const match = globalCss.match(new RegExp(`${cssVarName}\\s*:\\s*(-?\\d+)\\s*;`));
      expect(match, `${cssVarName} is not defined in global.css`).not.toBeNull();
      expect(Number(match![1])).toBe(value);
    });
  }
});
