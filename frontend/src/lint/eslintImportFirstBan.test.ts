// @vitest-environment node
import { Linter } from "eslint";
import { describe, expect, test } from "vitest";
// eslint.config.js is plain JS with no type declarations; the flat-config
// array shape is exactly what Linter.verify's `config` argument expects.
// @ts-expect-error -- untyped JS config module, see comment above.
import config from "../../eslint.config.js";

/**
 * Positive-control coverage for the no-restricted-syntax selector in
 * eslint.config.js that keeps imports together at the top of a module.
 * `eslint-plugin-import`'s `import/first` is not installed, so the rule is a
 * hand-written selector — and a hand-written selector is exactly the kind
 * that goes quietly wrong in both directions. The negative controls matter
 * as much as the positive one: a rule that fires on a directive prologue
 * demands a move with nowhere to move to.
 */
describe("the imports-first rule actually fires", () => {
  const linter = new Linter({ configType: "flat" });

  function ruleIds(code: string): string[] {
    const messages = linter.verify(code, config, { filename: "src/__fixture__.ts" });
    return messages.map((m) => m.ruleId).filter((id): id is string => id !== null);
  }

  test.each([
    ["a const before an import", 'const MAX = 3;\nimport { y } from "./y";\nexport const z = MAX + y;\n'],
    ["a function before an import", 'function f() {\n  return 1;\n}\nimport { y } from "./y";\nexport const z = f() + y;\n'],
    ["an import buried between other statements", 'import { a } from "./a";\nconst B = 2;\nimport { c } from "./c";\nexport const z = a + B + c;\n'],
  ])("%s is flagged", (_label, code) => {
    expect(ruleIds(code)).toContain("no-restricted-syntax");
  });

  test.each([
    ["imports in a contiguous block", 'import { a } from "./a";\nimport { b } from "./b";\nconst C = 3;\nexport const z = a + b + C;\n'],
    ["a type-only import beside a value import", 'import type { A } from "./a";\nimport { b } from "./b";\nexport const z: A = b;\n'],
    ["a side-effect import", 'import "./a.css";\nimport { b } from "./b";\nexport const z = b;\n'],
    // Must stay first in the file, so demanding the import move above it
    // would be an instruction with no correct fix.
    ["a directive prologue", '"use client";\nimport { b } from "./b";\nexport const z = b;\n'],
    // Part of the same dependency list an import belongs to.
    ["a re-export before an import", 'export { a } from "./a";\nimport { b } from "./b";\nexport const z = b;\n'],
    ["a star re-export before an import", 'export * from "./a";\nimport { b } from "./b";\nexport const z = b;\n'],
  ])("%s stays clean", (_label, code) => {
    expect(ruleIds(code)).toEqual([]);
  });
});
