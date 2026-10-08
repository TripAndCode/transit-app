// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

// Japanese has no spaces, so a one-line ellipsis cuts a route or thread name
// mid-word after a handful of characters. These labels wrap to a second line
// and clamp there instead.
const read = (p: string) =>
  readFileSync(path.resolve(process.cwd(), "src", p), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("two-line CJK clamp", () => {
  it("defines the .clamp-2 utility", () => {
    const body = ruleBody(read("styles/global.css"), ".clamp-2 {");
    expect(decl(body, "-webkit-line-clamp")).toBe("2");
    expect(decl(body, "display")).toBe("-webkit-box");
    expect(decl(body, "-webkit-box-orient")).toBe("vertical");
    expect(decl(body, "overflow")).toBe("hidden");
  });

  it.each([
    ["styles/overview.css", ".ov-pareto-label {"],
    ["styles/overview.css", ".ov-check-name {"],
    ["tabs/ask/investigation.css", ".investigation-steps button {"],
  ])("%s %s neither truncates to one line nor repeats the utility", (file, selector) => {
    const body = ruleBody(read(file), selector);
    expect(body).not.toMatch(/text-overflow:\s*ellipsis/);
    expect(decl(body, "-webkit-line-clamp")).toBeNull();
  });

  it.each([
    "components/RouteForecastSection.tsx",
    "components/ThreadSidebar.tsx",
    "components/ConcentrationBar.tsx",
    "components/RoutesToCheckList.tsx",
    "tabs/ask/InvestigationCanvas.tsx",
  ])(
    "%s uses .clamp-2 rather than an inline ellipsis",
    (file) => {
      const src = read(file);
      expect(src).not.toMatch(/textOverflow:\s*"ellipsis"/);
      expect(src).toMatch(/className="[^"]*\bclamp-2\b[^"]*"/);
    },
  );
});
