// @vitest-environment node
import { Linter } from "eslint";
import { describe, expect, test } from "vitest";
// eslint.config.js is plain JS with no type declarations; the flat-config
// array shape is exactly what Linter.verify's `config` argument expects.
// @ts-expect-error -- untyped JS config module, see comment above.
import config from "../../eslint.config.js";

/**
 * Positive- and negative-control coverage for the ban on touching web storage
 * outside src/utils/safeStorage.ts: storage throws where site data is blocked,
 * and the helper is what turns that into "nothing stored".
 */
describe("direct web-storage access is banned outside the safe-storage helper", () => {
  const linter = new Linter({ configType: "flat" });

  /** The messages this ban raised; no-restricted-properties holds other bans too. */
  function storageBans(code: string, filename = "src/__fixture__.ts"): string[] {
    const messages = linter.verify(code, config, { filename });
    expect(messages.filter((m) => m.fatal)).toEqual([]);
    return messages
      .filter((m) => m.message.includes("safeStorage"))
      .map((m) => m.ruleId)
      .filter((id): id is string => id !== null);
  }

  test.each([
    ["a bare localStorage call", 'export const v = localStorage.getItem("k");\n', "no-restricted-globals"],
    ["a bare sessionStorage call", 'sessionStorage.setItem("k", "v");\n', "no-restricted-globals"],
    ["the store passed along", "export const s: Storage = localStorage;\n", "no-restricted-globals"],
    ["window.localStorage", 'export const v = window.localStorage.getItem("k");\n', "no-restricted-properties"],
    ["window.sessionStorage", 'window.sessionStorage.removeItem("k");\n', "no-restricted-properties"],
    ["globalThis.localStorage", 'export const v = globalThis.localStorage.getItem("k");\n', "no-restricted-properties"],
    ["globalThis.sessionStorage", "globalThis.sessionStorage.clear();\n", "no-restricted-properties"],
  ])("%s is flagged", (_label, code, rule) => {
    expect(storageBans(code)).toContain(rule);
  });

  test("the name in a comment, a string or a type is not flagged", () => {
    const code =
      "// reads localStorage once\n" +
      'export const order = ["localStorage", "navigator"];\n' +
      "export type Area = Storage;\n";
    expect(storageBans(code)).toEqual([]);
  });

  test.each([["src/utils/safeStorage.ts"], ["src/api/x.test.ts"], ["src/components/X.test.tsx"], ["src/test/blockStorage.ts"]])(
    "%s may touch storage directly",
    (filename) => {
      expect(storageBans('export const v = localStorage.getItem("k");\n', filename)).toEqual([]);
    },
  );
});
