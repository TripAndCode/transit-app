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

  it("fills a hovered station or pin with a shade that reads against the surface in both themes", () => {
    expect(decl(ruleBody(css, ".rail-station:hover::before {"), "background")).toBe("var(--bg-soft)");
    // ".rail-pin:hover {" alone would match its earlier, unrelated mention
    // inside the combined ".rail-station:hover, .rail-stop:hover,
    // .rail-pin:hover {" text-decoration rule -- anchored on the closing
    // brace of ".rail-pin {" right before it, to reach this standalone rule.
    expect(decl(ruleBody(css, "}\n.rail-pin:hover {"), "background")).toBe("var(--bg-soft)");
  });
});
