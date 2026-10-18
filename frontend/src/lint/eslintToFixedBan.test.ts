// @vitest-environment node
import { Linter } from "eslint";
import { describe, expect, test } from "vitest";
// eslint.config.js is plain JS with no type declarations; the flat-config
// array shape is exactly what Linter.verify's `config` argument expects.
// @ts-expect-error -- untyped JS config module, see comment above.
import config from "../../eslint.config.js";

/**
 * Positive- and negative-control coverage for the no-restricted-syntax selector
 * in eslint.config.js that bans `.toFixed(1)`: `(-0.04).toFixed(1)` is "-0.0",
 * so one-decimal figures go through fixedTenth, which rounds first.
 */
describe("the toFixed(1) ban fires on what it names and nothing else", () => {
  const linter = new Linter({ configType: "flat" });

  function ruleIds(code: string): string[] {
    const messages = linter.verify(code, config, { filename: "src/__fixture__.ts" });
    return messages.map((m) => m.ruleId).filter((id): id is string => id !== null);
  }

  test.each([
    ["a member call", "export const f = (v: number): string => v.toFixed(1);\n"],
    ["an optional call", "export const f = (v?: number): string | undefined => v?.toFixed(1);\n"],
    ["a computed member call", 'export const f = (v: number): string => v["toFixed"](1);\n'],
  ])("%s is flagged", (_label, code) => {
    expect(ruleIds(code)).toContain("no-restricted-syntax");
  });

  test("another precision is not flagged", () => {
    expect(ruleIds("export const f = (v: number): string => v.toFixed(2);\n")).not.toContain("no-restricted-syntax");
  });

  test("fixedTenth's own call, under its disable comment, is not flagged", () => {
    const code =
      "export function fixedTenth(v: number): string {\n" +
      "  // eslint-disable-next-line no-restricted-syntax -- the one place a tenth is printed, after rounding\n" +
      "  return (Math.round(v * 10) / 10).toFixed(1);\n" +
      "}\n";
    expect(ruleIds(code)).not.toContain("no-restricted-syntax");
  });
});
