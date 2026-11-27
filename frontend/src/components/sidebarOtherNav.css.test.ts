import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./sidebarOtherNav.css"), "utf8");

describe("sidebarOtherNav.css", () => {
  it("gives the active Other link the same soft accent pill as the destinations above", () => {
    const active = ruleBody(css, ".app-sidebar-desktop .other-nav-link.active {");
    expect(decl(active, "background")).toBe("var(--accent-soft)");
    expect(decl(active, "color")).toBe("var(--accent-strong)");
  });

  it("gives a calm hover, outranking the global underline", () => {
    const hover = ruleBody(css, ".app-sidebar-desktop .other-nav-link:hover {");
    expect(decl(hover, "background")).toBe("var(--hover-tint)");
    expect(decl(hover, "text-decoration")).toBe("none");
  });
});
