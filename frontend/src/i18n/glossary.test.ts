// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

/** One word per thing: each entry is a variant the copy once used beside the
 *  preferred word for the same thing, so a reader met two names for one
 *  figure or document. */
const JA_VARIANTS: [variant: RegExp, preferred: string][] = [
  [/オンタイム率/, "定時率"],
  [/定時運行率/, "定時率"],
  [/運行達成率/, "運行率"],
  [/(?<!協)議会/, "協議会"],
  [/ブラウザー/, "ブラウザ"],
];
const EN_VARIANTS: [variant: RegExp, preferred: string][] = [[/Print \/ Save PDF/, "Print / Save as PDF"]];

const read = (path: string) => readFileSync(resolve(__dirname, path), "utf8");
const JA_SOURCES = {
  "ja.json": read("./locales/ja.json"),
  "design.ts": read("./design.ts"),
  "user-manual/ja.md": read("../../public/user-manual/ja.md"),
};
const EN_SOURCES = {
  "en.json": read("./locales/en.json"),
  "design.ts": read("./design.ts"),
  "user-manual/en.md": read("../../public/user-manual/en.md"),
};

describe("UI copy glossary", () => {
  it.each(JA_VARIANTS)("Japanese copy says %s only as its preferred word", (variant, preferred) => {
    for (const [file, text] of Object.entries(JA_SOURCES)) {
      expect(text.match(variant), `${file} uses ${variant.source}; say ${preferred}`).toBeNull();
    }
  });

  it.each(EN_VARIANTS)("English copy says %s only as its preferred phrase", (variant, preferred) => {
    for (const [file, text] of Object.entries(EN_SOURCES)) {
      expect(text.match(variant), `${file} uses ${variant.source}; say ${preferred}`).toBeNull();
    }
  });
});
