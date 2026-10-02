// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const css = readFileSync(fileURLToPath(new URL("./global.css", import.meta.url)), "utf8");

describe("global.css table wrapping", () => {
  it("keeps tables off body's overflow-wrap: anywhere, which lets a column shrink until words and numbers split", () => {
    expect(css).toMatch(/(?:^|\n)table\s*\{[^}]*overflow-wrap:\s*break-word;/);
  });

  it("draws the scroll shadow on the right edge only, where nothing pinned paints over it", () => {
    const block = css.match(/\.table-scroll\s*\{([^}]*)\}/);
    expect(block).not.toBeNull();
    expect(block![1]).toMatch(/right \//);
    expect(block![1]).not.toMatch(/left \//);
  });
});
