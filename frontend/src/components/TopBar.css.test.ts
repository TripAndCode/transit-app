import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./TopBar.css"), "utf8");

describe("TopBar.css", () => {
  it("makes the search trigger a full pill with a token-timed hover lift", () => {
    const base = ruleBody(css, ".topbar-search {");
    expect(decl(base, "border-radius")).toBe("999px");
    expect(decl(base, "transition")).toBe("box-shadow var(--transition)");
    const hover = ruleBody(css, ".topbar-search:hover {");
    expect(decl(hover, "box-shadow")).toBe("var(--el-1)");
  });

  it("hides the ⌘K hint below BP.sm, where there is no keyboard shortcut to show", () => {
    const phone = ruleBody(css, "@media (max-width: 640px)");
    expect(decl(ruleBody(phone, ".topbar-kbd {"), "display")).toBe("none");
  });
});
