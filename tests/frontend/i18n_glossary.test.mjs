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
  // The Saturday-Sunday day filter is 土日 on every screen; 土日祝 names the
  // weekend-and-holiday timetable, which is a different set of days.
  [/土日のみ/, "土日"],
  // One trip leaving one stop is an 観測, and its count is 観測数 on every
  // screen, in the manual and in the council report. サンプルデータ (sample
  // data in the landing page's mock-up) is a different thing and stays.
  [/観測データ数/, "観測数"],
  [/サンプル数/, "観測数"],
  [/計測回数/, "観測数"],
  [/計測(記録|が少な|の平均|から)|これまでの計測/, "観測"],
  [/少数サンプル|サンプルが少な/, "観測数が少ない"],
];
const EN_VARIANTS = [
  [/Print \/ Save PDF/, "Print / Save as PDF"],
  [/Continue with/, "Sign in with"],
  [/certificate(?! issued)/i, "Delay record (for reference)"],
  // The count of one trip leaving one stop is Observations wherever it is a
  // column, figure or glossary term, in prose as in a label. The admin
  // board's probe samples are a different count, so assertAbsent skips the
  // locale file's admin subtree for these.
  // Not a bare `samples:` key, which design.ts keeps as an identifier.
  [/\bsamples\b(?!\s*:)/i, "observations"],
  [/\bSample counts?\b/, "Observation counts"],
  [/\bmeasurements\b/i, "observations"],
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
function copyText(file, { skipAdmin = false } = {}) {
  const text = readFileSync(file, "utf8");
  if (file.endsWith(".json")) {
    const locale = JSON.parse(text);
    if (skipAdmin) delete locale.admin;
    return jsonValues(locale).join("\n");
  }
  if (file.endsWith(".md")) return text.replace(/\]\([^)]*\)/g, "]");
  return text;
}

function jsonValues(node) {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") return Object.values(node).flatMap(jsonValues);
  return [];
}

function assertAbsent(variants, sources, options) {
  for (const file of sources) {
    // A {{samples}} placeholder is a field name, not a word the reader sees.
    const text = copyText(file, options).replace(/\{\{[^}]*\}\}/g, " ");
    for (const [variant, preferred] of variants) {
      assert.equal(text.match(variant), null, `${file} uses ${variant.source}; say ${preferred}`);
    }
  }
}

test("glossary: Japanese copy uses one word per thing", () => assertAbsent(JA_VARIANTS, JA_SOURCES));

test("glossary: English copy uses one phrase per thing", () => assertAbsent(EN_VARIANTS, EN_SOURCES, { skipAdmin: true }));
