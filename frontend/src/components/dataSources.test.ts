import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { creditText, DATA_SOURCES, dataSourceOf } from "./dataSources";

/** The plain-text credit per feed and language, shared with the backend's
 *  tests/unit/test_data_sources.py so a CSV cell and a printed page credit a
 *  source in the same words. */
const EXPECTED: Record<string, Record<"ja" | "en", string>> = JSON.parse(
  readFileSync(resolve(__dirname, "../../../tests/fixtures/data_source_credit_lines.json"), "utf8"),
);

describe("creditText", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it.each(["ja", "en"] as const)("follows each linked name with its URL (%s)", async (locale) => {
    await i18n.changeLanguage(locale);
    for (const [feedUrl, lines] of Object.entries(EXPECTED)) {
      const source = dataSourceOf(feedUrl);
      if (!source) throw new Error(`no credited data source covers ${feedUrl}`);
      expect(creditText(source, i18n.t, locale), feedUrl).toBe(lines[locale]);
    }
  });

  // A URL in parentheses inside another parenthesis reads as part of the name
  // around it, so no credit nests one.
  it.each(["ja", "en"] as const)("never nests parentheses (%s)", async (locale) => {
    await i18n.changeLanguage(locale);
    for (const source of DATA_SOURCES) {
      const text = creditText(source, i18n.t, locale);
      let depth = 0;
      let deepest = 0;
      for (const char of text) {
        if (char === "（" || char === "(") deepest = Math.max(deepest, ++depth);
        else if (char === "）" || char === ")") depth--;
      }
      expect(deepest, text).toBe(1);
    }
  });
});
