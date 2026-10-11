import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("index.html", () => {
  it("declares its charset inside the first 1024 bytes", () => {
    // Browsers only honour a <meta charset> found in the first 1024 bytes
    // when the host serves no charset in Content-Type, so it has to precede
    // the inline scripts (which carry non-ASCII text).
    const html = readFileSync(resolve(process.cwd(), "index.html"));
    const offset = html.indexOf('<meta charset="UTF-8"');
    expect(offset).toBeGreaterThanOrEqual(0);
    expect(offset).toBeLessThan(1024 - '<meta charset="UTF-8" />'.length);
  });
});
