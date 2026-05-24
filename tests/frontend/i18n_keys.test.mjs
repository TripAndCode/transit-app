import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

function flatKeys(obj, prefix = "") {
  if (obj === null || typeof obj !== "object") return prefix ? [prefix] : [];
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    const next = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatKeys(v, next));
    } else {
      out.push(next);
    }
  }
  return out;
}

test("ja and en have the same key set", () => {
  const ja = JSON.parse(readFileSync("frontend/src/i18n/locales/ja.json", "utf8"));
  const en = JSON.parse(readFileSync("frontend/src/i18n/locales/en.json", "utf8"));
  const jaKeys = new Set(flatKeys(ja));
  const enKeys = new Set(flatKeys(en));
  const missingInEn = [...jaKeys].filter((k) => !enKeys.has(k));
  const missingInJa = [...enKeys].filter((k) => !jaKeys.has(k));
  assert.deepEqual(missingInEn, [], `Missing in en.json: ${missingInEn.join(", ")}`);
  assert.deepEqual(missingInJa, [], `Missing in ja.json: ${missingInJa.join(", ")}`);
});
