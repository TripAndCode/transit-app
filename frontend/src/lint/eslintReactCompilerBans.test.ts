import { Linter } from "eslint";
import { describe, expect, test } from "vitest";
// eslint.config.js is plain JS with no type declarations; the flat-config
// array shape is exactly what Linter.verify's `config` argument expects.
// @ts-expect-error -- untyped JS config module, see comment above.
import config from "../../eslint.config.js";

/**
 * Positive-control coverage for the three no-restricted-* rules in
 * eslint.config.js that ban manual memoization (React Compiler handles it
 * automatically — see that file's comments for the full rationale). Runs
 * the real flat config through ESLint's Linter API against small violating
 * fixtures, so a future edit that silently defangs one of these rules (e.g.
 * a typo'd selector or a dropped importNames entry) fails a test instead of
 * only being noticed the next time someone happens to write the banned
 * pattern by hand.
 */
describe("react compiler manual-memoization bans actually fire", () => {
  const linter = new Linter({ configType: "flat" });

  function ruleIds(code: string): string[] {
    const messages = linter.verify(code, config, { filename: "src/__fixture__.tsx" });
    return messages.map((m) => m.ruleId).filter((id): id is string => id !== null);
  }

  test("a bare useMemo/useCallback call is flagged (no-restricted-syntax)", () => {
    const code = 'import { useMemo } from "react";\nfunction f() {\n  return useMemo(() => 1, []);\n}\n';
    expect(ruleIds(code)).toContain("no-restricted-syntax");
  });

  test("an aliased import is flagged even though the call site uses the alias, not the banned name (no-restricted-imports)", () => {
    const code = 'import { useCallback as uc } from "react";\nconst g = uc(() => {}, []);\n';
    const ids = ruleIds(code);
    expect(ids).toContain("no-restricted-imports");
    // The bare-identifier selector matches on the LOCAL name ("uc"), not the
    // imported name, so it must NOT also fire here -- this fixture isolates
    // no-restricted-imports as the rule actually closing the aliased-import
    // hole, not an incidental double-flag from no-restricted-syntax.
    expect(ids).not.toContain("no-restricted-syntax");
  });

  test("a React.memo-style member-expression call is flagged (no-restricted-properties)", () => {
    const code = 'import React from "react";\nfunction Foo() { return null; }\nconst C = React.memo(Foo);\n';
    const ids = ruleIds(code);
    expect(ids).toContain("no-restricted-properties");
    // A default import resolves to the specifier name "default", which isn't
    // in no-restricted-imports' importNames list -- isolates member-expression
    // coverage to no-restricted-properties, not an incidental double-flag.
    expect(ids).not.toContain("no-restricted-imports");
  });

  test("ordinary code with no manual memoization is clean", () => {
    const code = "export function f() {\n  return 1 + 1;\n}\n";
    expect(ruleIds(code)).toEqual([]);
  });
});
