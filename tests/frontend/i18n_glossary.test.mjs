import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// One word per thing: each entry is a variant the copy once used beside the
// preferred word for the same thing, so a reader met two names for one
// figure, document or button. Checked across the locale files, the design
// strings, the user manuals and the legal pages, which all show the same names.
const JA_VARIANTS = [
  [/オンタイム率/, "定時率"],
  [/定時運行率/, "定時率"],
  [/運行達成率/, "運行率"],
  [/(?<!協)議会/, "協議会"],
  [/ブラウザー/, "ブラウザ"],
  [/印刷 \/ PDF保存/, "印刷 / PDFで保存"],
  [/サインイン/, "ログイン"],
  [/サインアウト/, "ログアウト"],
  [/そのほか/, "その他"],
  // The service's own record of a late departure is never called a
  // certificate or proof; the copy names the operator's certificate only to
  // say the record is not one.
  [/証明(?!書では)/, "遅延の記録（参考）"],
  // The time a trip spends at a stop is 滞留 wherever the dwell figures
  // are named; 停車 stays only for other things (a stopping pattern, the
  // holds behind the time-adjustment rate).
  [/停留所での停車/, "滞留"],
];
const EN_VARIANTS = [
  [/Print \/ Save PDF/, "Print / Save as PDF"],
  [/Continue with/, "Sign in with"],
  [/certificate(?! issued)/i, "Delay record (for reference)"],
];

const JA_SOURCES = [
  "frontend/src/i18n/locales/ja.json",
  "frontend/src/i18n/design.ts",
  "frontend/public/user-manual/ja.md",
  "frontend/public/legal/terms.ja.md",
  "frontend/public/legal/privacy.ja.md",
];
const EN_SOURCES = [
  "frontend/src/i18n/locales/en.json",
  "frontend/src/i18n/design.ts",
  "frontend/public/user-manual/en.md",
  "frontend/public/legal/terms.en.md",
  "frontend/public/legal/privacy.en.md",
];

/** The words a reader sees in a file: a locale file's values, not its keys,
 *  and a Markdown page's prose, not its link and image targets. */
function copyText(file) {
  const text = readFileSync(file, "utf8");
  if (file.endsWith(".json")) return jsonValues(JSON.parse(text)).join("\n");
  if (file.endsWith(".md")) return text.replace(/\]\([^)]*\)/g, "]");
  return text;
}

function jsonValues(node) {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") return Object.values(node).flatMap(jsonValues);
  return [];
}

function assertAbsent(variants, sources) {
  for (const file of sources) {
    const text = copyText(file);
    for (const [variant, preferred] of variants) {
      assert.equal(text.match(variant), null, `${file} uses ${variant.source}; say ${preferred}`);
    }
  }
}

test("glossary: Japanese copy uses one word per thing", () => assertAbsent(JA_VARIANTS, JA_SOURCES));

test("glossary: English copy uses one phrase per thing", () => assertAbsent(EN_VARIANTS, EN_SOURCES));
