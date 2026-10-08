import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const LEGAL_DIR = resolve(__dirname, "../../public/legal");

describe.each(["privacy", "terms"])("public/legal/%s", (doc) => {
  it.each(["ja", "en"])("has a %s version with a title and no unfilled placeholder", (locale) => {
    const text = readFileSync(resolve(LEGAL_DIR, `${doc}.${locale}.md`), "utf8");
    expect(text.startsWith("# ")).toBe(true);
    expect(text).not.toMatch(/\{\{[A-Z_]+\}\}/);
  });
});

describe.each(["privacy", "terms"])("public/legal/%s naming", (doc) => {
  it.each([
    ["ja", "遅延ダッシュボード"],
    ["en", "Delay Dashboard"],
  ])("calls the service by the name the app shows (%s)", (locale, name) => {
    const text = readFileSync(resolve(LEGAL_DIR, `${doc}.${locale}.md`), "utf8");
    expect(text).toContain(name);
    expect(text).not.toContain("Transit Delay App");
  });

  it("quotes with typographic marks in English", () => {
    const text = readFileSync(resolve(LEGAL_DIR, `${doc}.en.md`), "utf8");
    expect(text).toContain("“the Service”");
    expect(text).not.toMatch(/"the Service"/);
  });
});
