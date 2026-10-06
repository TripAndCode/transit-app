import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// One word per thing: each entry is a variant the copy once used beside the
// preferred word for the same thing, so a reader met two names for one
// figure, document or button. Checked across the locale files, the design
// strings and the user manuals, which all show the same names.
const JA_VARIANTS = [
  [/オンタイム率/, "定時率"],
  [/定時運行率/, "定時率"],
  [/運行達成率/, "運行率"],
  [/(?<!協)議会/, "協議会"],
  [/ブラウザー/, "ブラウザ"],
  [/印刷 \/ PDF保存/, "印刷 / PDFで保存"],
];
const EN_VARIANTS = [[/Print \/ Save PDF/, "Print / Save as PDF"]];

const JA_SOURCES = [
  "frontend/src/i18n/locales/ja.json",
  "frontend/src/i18n/design.ts",
  "frontend/public/user-manual/ja.md",
];
const EN_SOURCES = [
  "frontend/src/i18n/locales/en.json",
  "frontend/src/i18n/design.ts",
  "frontend/public/user-manual/en.md",
];

function assertAbsent(variants, sources) {
  for (const file of sources) {
    const text = readFileSync(file, "utf8");
    for (const [variant, preferred] of variants) {
      assert.equal(text.match(variant), null, `${file} uses ${variant.source}; say ${preferred}`);
    }
  }
}

test("glossary: Japanese copy uses one word per thing", () => assertAbsent(JA_VARIANTS, JA_SOURCES));

test("glossary: English copy uses one phrase per thing", () => assertAbsent(EN_VARIANTS, EN_SOURCES));
