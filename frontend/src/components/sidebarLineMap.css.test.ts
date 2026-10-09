import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./sidebarLineMap.css"), "utf8");

describe("sidebarLineMap.css", () => {
  it("keeps every rail link free of the global hover underline", () => {
    const hover = ruleBody(css, ".rail-station:hover,\n.rail-stop:hover,\n.rail-pin:hover {");
    expect(decl(hover, "text-decoration")).toBe("none");
  });
});
