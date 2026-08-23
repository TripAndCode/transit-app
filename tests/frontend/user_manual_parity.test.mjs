import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

const MANUAL_DIR = "frontend/public/user-manual";

// Trip-wire for the two-tier i18n system frontend/public/user-manual/{en,ja}.md
// use: unlike src/i18n/locales/{en,ja}.json, no lint checks this content for
// drift (tests/frontend/i18n_keys.test.mjs and scripts/lint-i18n-strings.py
// both only inspect src/**/*.ts(x) and the locale JSON files). This can't
// verify translation *quality*, but it does catch the gross case of one
// locale's manual gaining/losing a whole section or image without the other.

function headingCount(markdown, level) {
  const marker = "#".repeat(level) + " ";
  return markdown.split("\n").filter((line) => line.startsWith(marker)).length;
}

// Screenshots are locale-specific (e.g. ./02-overview.ja.png vs .en.png --
// same UI, captured with the app in each language) -- strip the .ja/.en
// suffix so this still catches a genuinely missing/extra image without
// false-flagging on the expected per-locale filename difference.
function imagePaths(markdown) {
  return [...markdown.matchAll(/!\[[^\]]*]\((\.\/[^)]+)\)/g)].map((m) => m[1]);
}

function normalizedFilenames(paths) {
  return paths.map((p) => p.replace(/\.(en|ja)\.png$/, ".png")).sort();
}

test("user manual: en and ja have the same section structure", () => {
  const en = readFileSync(`${MANUAL_DIR}/en.md`, "utf8");
  const ja = readFileSync(`${MANUAL_DIR}/ja.md`, "utf8");

  assert.equal(headingCount(en, 1), headingCount(ja, 1), "top-level (#) heading count differs");
  assert.equal(headingCount(en, 2), headingCount(ja, 2), "section (##) heading count differs");
  assert.equal(headingCount(en, 3), headingCount(ja, 3), "subsection (###) heading count differs");
  assert.deepEqual(
    normalizedFilenames(imagePaths(en)),
    normalizedFilenames(imagePaths(ja)),
    "referenced screenshot filenames differ",
  );
});

test("user manual: every referenced screenshot exists and matches its own locale", () => {
  for (const [locale, file] of [["en", "en.md"], ["ja", "ja.md"]]) {
    const markdown = readFileSync(`${MANUAL_DIR}/${file}`, "utf8");
    for (const relPath of imagePaths(markdown)) {
      assert.match(
        relPath,
        new RegExp(`\\.${locale}\\.png$`),
        `${file} references ${relPath}, which isn't a .${locale}.png screenshot -- ` +
          `screenshots must match the UI language of the manual they're embedded in`,
      );
      const diskPath = `${MANUAL_DIR}/${relPath.replace(/^\.\//, "")}`;
      assert.ok(existsSync(diskPath), `${file} references ${relPath}, but ${diskPath} doesn't exist`);
    }
  }
});
