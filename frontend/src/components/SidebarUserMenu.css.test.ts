import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./SidebarUserMenu.css"), "utf8");

describe("SidebarUserMenu.css", () => {
  it("gives the account trigger a calm hover fill", () => {
    const hover = ruleBody(css, ".sidebar-user-trigger:hover {");
    expect(decl(hover, "background")).toBe("var(--hover-tint)");
  });

  it("highlights a menu row the same way on hover and keyboard focus", () => {
    const hover = ruleBody(css, ".sidebar-user-menu-item:hover,\n.sidebar-user-menu-item:focus-visible {");
    expect(decl(hover, "background")).toBe("var(--bg-soft)");
  });
});
