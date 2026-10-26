import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { DATA_SOURCES } from "../components/dataSources";

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

describe("public/legal/terms data-source credits", () => {
  const LOCALES_DIR = resolve(__dirname, "../i18n/locales");
  // `[text](url)` to `text`, allowing one level of brackets inside the text.
  const linkText = (markdown: string) => markdown.replace(/\[((?:[^[\]]|\[[^\]]*\])*)\]\([^)]*\)/g, "$1");
  const untagged = (translation: string) => translation.replace(/<\/?[a-z]+>/g, "");

  it.each(["ja", "en"])("states each credit exactly as the app does (%s)", (locale) => {
    const terms = linkText(readFileSync(resolve(LEGAL_DIR, `terms.${locale}.md`), "utf8"));
    const strings: unknown = JSON.parse(readFileSync(resolve(LOCALES_DIR, `${locale}.json`), "utf8"));
    for (const { creditKey } of DATA_SOURCES) {
      const credit = creditKey.split(".").reduce<unknown>((node, key) => (node as Record<string, unknown>)[key], strings);
      expect(typeof credit, creditKey).toBe("string");
      expect(terms).toContain(untagged(credit as string));
    }
  });

  // Weather and map tiles carry their own credits where they are shown; the
  // terms name them so the section doesn't read as the complete list without them.
  it.each([
    ["ja", ["気象庁", "国土地理院", "OpenStreetMap"]],
    ["en", ["Japan Meteorological Agency", "Geospatial Information Authority of Japan", "OpenStreetMap"]],
  ])("names the sources credited in place (%s)", (locale, sources) => {
    const terms = readFileSync(resolve(LEGAL_DIR, `terms.${locale}.md`), "utf8");
    for (const source of sources) expect(terms).toContain(source);
  });

  it.each(["ja", "en"] as const)("links each source's data and licence deed as the app does (%s)", (locale) => {
    const terms = readFileSync(resolve(LEGAL_DIR, `terms.${locale}.md`), "utf8");
    for (const source of DATA_SOURCES) {
      for (const url of Object.values(source.links(locale))) expect(terms, source.creditKey).toContain(`](${url})`);
    }
  });
});
